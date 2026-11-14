"""Research SQL integration in the disposable test DB, with no model/network calls."""

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.cli.chunk_recipe_parents import split_parent
from app.db.session import engine
from app.services import research_retrieval
from app.services.research_index import (
    DIMENSIONS,
    INSTRUCTION_VERSION,
    MODEL_NAME,
    QUERY_PREFIX,
    insert_batch,
    insert_chunk_vectors,
    mark_batch,
    upsert_parents,
)
from app.services.research_retrieval import RecipeRetriever, RetrievalError

PROFILE = {
    "model_name": MODEL_NAME,
    "dimensions": DIMENSIONS,
    "query_prefix": QUERY_PREFIX,
    "instruction_version": INSTRUCTION_VERSION,
    "normalize": True,
    "max_position_embeddings": 512,
}
VECTOR = [1.0] + [0.0] * 511


@pytest.fixture
def corpus():
    assert engine.url.database.endswith("_test")
    path = Path(__file__).parents[1] / "migrations/versions/l7a8b9c0d1e2_recipe_research_staging.py"
    spec = importlib.util.spec_from_file_location("research_test_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    parent = {
        "document_id": "test-parent",
        "name": "番茄炒蛋",
        "source_asset_key": "test/recipes.json",
        "source_sha256": "a" * 64,
        "source_record_key": "row:1",
        "source_kind": "test",
        "source_version": 1,
        "publishable": False,
        "status": "loaded",
        "capabilities": {"chunkable": True, "publishable_candidate": True},
        "fields": {},
        "ingredients": [{"name": "番茄"}, {"name": "鸡蛋"}],
        "steps": [{"ordinal": 1, "text": "炒熟鸡蛋后加入番茄翻炒。"}],
    }
    chunks = split_parent(parent, max_chars=450, policy_version="recipe-structure-v1")
    try:
        with engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            upsert_parents(connection, [parent])
            insert_batch(
                connection, "batch-one", PROFILE, "b" * 64, "c" * 64, "recipe-structure-v1"
            )
            insert_batch(
                connection, "batch-two", PROFILE, "d" * 64, "e" * 64, "recipe-structure-v1"
            )
            insert_chunk_vectors(connection, "batch-one", chunks, [VECTOR])
            for batch in ("batch-one", "batch-two"):
                mark_batch(
                    connection, batch, status="ready_for_evaluation", error=None, counts=(1, 1)
                )
        yield parent, chunks[0]
    finally:
        # This schema is owned by this fixture in the disposable _test database only.
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP SCHEMA IF EXISTS recipe_research CASCADE")


def retriever(batch="batch-one"):
    return RecipeRetriever(engine=engine, model_dir=Path("no-model"), batch_id=batch)


def test_real_hybrid_fts_filter_and_model_reuse(corpus, monkeypatch):
    created = []

    class Encoder:
        def __init__(self, path):
            created.append(path)
            self.profile = PROFILE

        def count_tokens(self, value):
            return 10

        def embed_queries(self, values):
            assert engine.pool.checkedout() == 0  # Inference owns no DB transaction.
            return [VECTOR]

    monkeypatch.setattr(research_retrieval, "BgeEmbedder", Encoder)
    service = retriever()
    result = service.search_all("番茄炒蛋")
    assert result["statuses"] == {"vector": "ok", "fts": "ok", "hybrid": "ok"}
    hit = result["hybrid"][0]
    assert hit["rrf_score"] == pytest.approx(2 / 61)
    assert set(hit["channels"]) == {"vector", "fts"}
    assert hit["document_id"] == corpus[0]["document_id"]
    service.search("鸡蛋", mode="vector")
    assert len(created) == 1
    fts = retriever()
    empty = fts.search("zzqv9817", mode="fts")
    assert empty["status"] == "empty" and empty["hits"] == []
    assert fts._embedder is None


def test_detail_is_scoped_to_batch_and_version(corpus):
    parent, chunk = corpus
    service = retriever()
    result = service.get_detail(parent["document_id"], chunk["content_version"])
    assert result["document"]["payload"] == parent
    assert service._embedder is None
    assert (
        retriever("batch-two").get_detail(parent["document_id"], chunk["content_version"])[
            "document"
        ]
        is None
    )
    with pytest.raises(RetrievalError, match="evidence_mismatch"):
        service.get_detail(parent["document_id"], "0" * 64)


def test_changed_parent_is_never_mixed_with_old_chunks(corpus):
    parent, chunk = corpus
    changed = deepcopy(parent)
    changed["steps"][0]["text"] = "内容已经改变。"
    with engine.begin() as connection:
        upsert_parents(connection, [changed])
    with pytest.raises(RetrievalError, match="evidence_mismatch"):
        retriever().get_detail(parent["document_id"], chunk["content_version"])
    with pytest.raises(RetrievalError, match="evidence_mismatch"):
        retriever().search("番茄炒蛋", mode="fts")


def test_batch_profile_is_checked_even_for_fts(corpus):
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE recipe_research.build_batches SET query_prefix = :prefix WHERE id = :id"),
            {"prefix": "wrong", "id": "batch-one"},
        )
    with pytest.raises(RetrievalError, match="index_config"):
        retriever().search("鸡蛋", mode="fts")
