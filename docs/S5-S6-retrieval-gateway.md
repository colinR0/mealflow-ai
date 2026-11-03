# S5 → S6 retrieval gateway

`app.services.retrieval_gateway.search` accepts **exactly one** of `sessions=` or
`connection=`. It does not import the global SessionLocal, open a second database URL,
install extensions, or silently fall back. A supplied connection must be idle; the gateway
owns short read transactions, with embedding calls outside transactions. Engine search_path
and schema_translate_map are preserved when sessions supplies the binding.

Configuration (also in `.env.example`):

```dotenv
RETRIEVAL_MODE=demo
RETRIEVAL_EMBEDDING_MODEL=fake
RETRIEVAL_EMBEDDING_DIMENSIONS=64
RETRIEVAL_TEMPLATE_VERSION=v1
RETRIEVAL_TOKENIZER_VERSION=v1
```

`demo` is the offline default, explicitly reporting `data.fake=true` and a fake embedding
warning. It retrieves actual approved, published, current-version chunks in the active
generation and applies the demo keyword/vector RRF; it never invents source/chunk IDs.

`native` delegates rankings to the existing `NativeRetriever` PostgreSQL full-text/vector
SQL on the **same supplied database**. The active ready generation's profile must match
the configured model/dimensions/template/tokenizer, plus the injected query provider's
identity and fake flag, exactly. The default query embedder only accepts the fake 64-dimensional
v1/v1 profile. Real embeddings require explicitly injected `QueryEmbedder`; no cloud provider
is selected automatically. Profile IDs are read from the current DB rather than hardcoded.

No active generation, profile mismatch, absent vector, provider failure or changed generation
returns `ToolResult(status='unavailable', error_code=...)`, never demo fallback or empty success.
Embedding dimensions and finite numeric elements are validated. Returned evidence contains
the actual recipe/content version/chunk/source/excerpt plus content/capabilities and index
profile/generation metadata. Native ranks are hydrated only for returned chunks and checked
against the complete coherent hit tuple. Approval/version predicates apply to hydration too.

Integration verification uses isolated temporary schemas with:

```powershell
$env:MEALFLOW_TEST_NATIVE_POSTGRES_URL='postgresql+psycopg://mealflow@127.0.0.1:5434/mealflow_vector'
$env:MEALFLOW_TEST_POSTGRES_URL='postgresql+psycopg://mealflow@localhost:5433/mealflow_test'
.\.venv\Scripts\python.exe -m pytest apps/api/tests/test_retrieval_gateway.py -q
```

PG16+vector verifies native ranking/evidence/provider boundaries. PG17 without vector verifies
an explicit unavailable result rather than fallback. SQLite tests cover demo evidence,
revocation/version changes, every profile identity field, invalid embeddings and index changes.

Verification on 2026-11-03: gateway tests **16 passed**; gateway plus existing native PG tests
**26 passed in 30.31s**, including actual PG16/vector and PG17/no-vector runs. Ruff passes.
One earlier full-suite run had 205 passes and a native-index test failing to acquire its
global profile advisory lock while concurrent validation was active; the combined isolated
rerun above passed. That run is not recorded as a full-suite pass.

## Graph integration handoff

The constraint agent owns `durable_graph.py` during its active changes. This gateway change
does not edit that file before the owner finishes. Once released, the only required graph
replacement is the existing `Runtime.search_recipes` body:

```python
from app.services import retrieval_gateway

async def search_recipes(self, body):
    return retrieval_gateway.search(body.query, sessions=self.sessions)
```

Keep the surrounding constraint, lease, timeout, evidence validation and fact-version code.
Do not replace its latest graph file with an older copy. The gateway returns ToolResult
directly, including evidence IDs, warnings and data_versions for the existing tool loop.
