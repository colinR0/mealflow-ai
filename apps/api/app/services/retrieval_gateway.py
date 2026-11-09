"""S5/S6 boundary: explicit retrieval mode and injected database/provider dependencies.

No alternate database and no fallback. A supplied Connection must be idle: this module
owns short read transactions and calls embedding outside them. Sessions supplies its own
bound connection (including the caller's schema mapping), never global SessionLocal.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import Connection, select, text

from app.agent.tool_runtime import ToolResult
from app.core.config import Settings, get_settings
from app.db.v2_models import (
    EmbeddingProfile,
    IndexGeneration,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeSource,
)
from app.services.hybrid_retrieval import (
    RetrievalHit,
    cosine,
    fake_embedding,
    reciprocal_rank_fusion,
)
from app.services.local_embedding import LocalEmbedding
from app.services.native_retrieval import NativeRetriever, vector_literal


class RetrievalUnavailable(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class QueryEmbedder:
    embed_query: Callable[[str], Sequence[float]]
    model: str
    dimensions: int
    template_version: str
    tokenizer_version: str
    fake: bool
    embed_document: Callable[[str], Sequence[float]] | None = None


FAKE_EMBEDDER = QueryEmbedder(
    fake_embedding, "fake", 64, "v1", "v1", True, embed_document=fake_embedding
)


@lru_cache(maxsize=4)
def _local_provider(
    model: str,
    dimensions: int,
    template_version: str,
    tokenizer_version: str,
    query_prefix: str,
    device: str,
    batch_size: int,
    local_files_only: bool,
    max_seq_length: int | None,
) -> QueryEmbedder:
    local = LocalEmbedding(
        model_name=model,
        dimensions=dimensions,
        query_prefix=query_prefix,
        device=device,
        batch_size=batch_size,
        local_files_only=local_files_only,
        max_seq_length=max_seq_length,
    )
    return QueryEmbedder(
        local.embed_query,
        model,
        dimensions,
        template_version,
        tokenizer_version,
        False,
        embed_document=local.embed_document,
    )


def configured_embedder(settings: Settings) -> QueryEmbedder:
    """Build the provider selected by settings; weights load on first inference."""
    if settings.retrieval_embedding_model == "fake":
        return FAKE_EMBEDDER
    return _local_provider(
        settings.retrieval_embedding_model,
        settings.retrieval_embedding_dimensions,
        settings.retrieval_template_version,
        settings.retrieval_tokenizer_version,
        settings.retrieval_embedding_query_prefix,
        settings.retrieval_embedding_device,
        settings.retrieval_embedding_batch_size,
        settings.retrieval_embedding_local_files_only,
        settings.retrieval_embedding_max_seq_length,
    )


def document_embedder(provider: QueryEmbedder) -> Callable[[str], Sequence[float]]:
    return provider.embed_document or provider.embed_query


@contextmanager
def database_connection(*, sessions=None, connection=None):
    if (sessions is None) == (connection is None):
        raise ValueError("Pass exactly one of sessions or connection")
    if connection is not None:
        if connection.in_transaction():
            raise ValueError("Retrieval requires an idle connection; it owns short transactions")
        yield connection
    else:
        with sessions() as session:
            # get_bind preserves Engine execution options such as schema_translate_map.
            with session.get_bind().connect() as owned:
                yield owned


def active_profile(connection, settings, provider):
    row = (
        connection.execute(
            select(
                IndexGeneration.id.label("generation_id"),
                EmbeddingProfile.id.label("profile_id"),
                EmbeddingProfile.model,
                EmbeddingProfile.dimensions,
                EmbeddingProfile.template_version,
                EmbeddingProfile.tokenizer_version,
                EmbeddingProfile.is_fake,
            )
            .join(EmbeddingProfile, EmbeddingProfile.id == IndexGeneration.profile_id)
            .where(IndexGeneration.active.is_(True), IndexGeneration.status == "ready")
        )
        .mappings()
        .first()
    )
    if row is None:
        raise RetrievalUnavailable("NO_ACTIVE_INDEX")
    expected = (
        settings.retrieval_embedding_model,
        settings.retrieval_embedding_dimensions,
        settings.retrieval_template_version,
        settings.retrieval_tokenizer_version,
    )
    identity = (
        provider.model,
        provider.dimensions,
        provider.template_version,
        provider.tokenizer_version,
    )
    indexed = (row["model"], row["dimensions"], row["template_version"], row["tokenizer_version"])
    if identity != expected or indexed != expected or row["is_fake"] != provider.fake:
        raise RetrievalUnavailable("EMBEDDING_PROFILE_MISMATCH")
    if provider.embed_query is fake_embedding and (
        identity != ("fake", 64, "v1", "v1") or not row["is_fake"]
    ):
        raise RetrievalUnavailable("FAKE_PROFILE_REQUIRED")
    if settings.retrieval_mode == "demo" and not provider.fake:
        raise RetrievalUnavailable("DEMO_REQUIRES_FAKE_PROVIDER")
    return dict(row)


def evidence_rows(connection, generation_id, chunk_ids=None, document_type=None):
    statement = (
        select(
            KnowledgeDocument.recipe_id,
            KnowledgeDocument.id.label("document_id"),
            KnowledgeDocument.document_type,
            KnowledgeDocument.title,
            KnowledgeDocument.content_version,
            KnowledgeDocument.content,
            KnowledgeDocument.capabilities,
            KnowledgeChunk.id.label("chunk_id"),
            KnowledgeChunk.text.label("excerpt"),
            KnowledgeChunk.embedding,
            KnowledgeSource.id.label("source_id"),
            KnowledgeSource.url,
        )
        .join(KnowledgeChunk, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .join(KnowledgeSource, KnowledgeSource.id == KnowledgeDocument.source_id)
        .where(
            KnowledgeChunk.generation_id == generation_id,
            KnowledgeChunk.content_version == KnowledgeDocument.content_version,
            KnowledgeDocument.published.is_(True),
            KnowledgeSource.approval == "approved",
        )
    )
    if chunk_ids is not None:
        statement = statement.where(KnowledgeChunk.id.in_(chunk_ids))
    if document_type is not None:
        statement = statement.where(KnowledgeDocument.document_type == document_type)
    return connection.execute(statement).mappings().all()


def demo_hits(rows, query, embedding, limit):
    keyword, vector = [], []
    for row in rows:
        overlap = sum(term in row["excerpt"].lower() for term in query.lower().split())
        if overlap:
            keyword.append((overlap, row))
        similarity = cosine(embedding, row["embedding"])
        if similarity > 0 and (row["document_type"] != "cooking_skill" or overlap):
            vector.append((similarity, row))
    channels = []
    best = {}
    for candidates in (keyword, vector):
        candidates.sort(key=lambda item: (-item[0], item[1]["document_id"], item[1]["chunk_id"]))
        hits = []
        for rank, (score, row) in enumerate(candidates[:40], 1):
            hits.append(
                RetrievalHit(row["document_id"], row["chunk_id"], rank, score, row["excerpt"])
            )
            choice = (rank, row["chunk_id"])
            if row["document_id"] not in best or choice < best[row["document_id"]][0]:
                best[row["document_id"]] = (choice, row)
        channels.append(hits)
    return [
        (best[recipe_id][1], score) for recipe_id, score in reciprocal_rank_fusion(channels)[:limit]
    ]


def search(
    query: str,
    *,
    sessions=None,
    connection: Connection | None = None,
    settings: Settings | None = None,
    provider: QueryEmbedder | None = None,
    limit: int = 8,
    document_type: str | None = None,
) -> ToolResult:
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        return ToolResult(status="error", error_code="INVALID_QUERY")
    if type(limit) is not int or not 1 <= limit <= 100:
        return ToolResult(status="error", error_code="INVALID_LIMIT")
    if document_type not in {None, "recipe", "cooking_skill"}:
        return ToolResult(status="error", error_code="INVALID_DOCUMENT_TYPE")
    settings = settings or get_settings()
    provider = provider or configured_embedder(settings)
    try:
        with database_connection(sessions=sessions, connection=connection) as db:
            if settings.retrieval_mode == "native" and db.dialect.name != "postgresql":
                raise RetrievalUnavailable("NATIVE_REQUIRES_POSTGRESQL")
            with db.begin():
                profile = active_profile(db, settings, provider)
                if settings.retrieval_mode == "native":
                    available = db.scalar(
                        text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
                    )
                    if not available:
                        raise RetrievalUnavailable("NATIVE_VECTOR_UNAVAILABLE")
            # No DB transaction spans a possibly remote/local embedding request.
            try:
                embedding = provider.embed_query(query)
            except Exception as exc:
                raise RetrievalUnavailable("EMBEDDING_PROVIDER_FAILED") from exc
            if len(embedding) != provider.dimensions:
                raise RetrievalUnavailable("EMBEDDING_DIMENSION_MISMATCH")
            vector_literal(embedding)  # Reject bool, NaN, infinity and nonnumeric values.
            with db.begin():
                current = active_profile(db, settings, provider)
                if current != profile:
                    raise RetrievalUnavailable("INDEX_CHANGED")
                if settings.retrieval_mode == "native":
                    hits = NativeRetriever(
                        db, profile_id=profile["profile_id"], dimensions=profile["dimensions"]
                    ).search(query, embedding, limit, document_type)
                    rows = evidence_rows(
                        db, profile["generation_id"], [hit.chunk_id for hit in hits], document_type
                    )
                    by_chunk = {row["chunk_id"]: row for row in rows}
                    ranked = []
                    for hit in hits:
                        row = by_chunk.get(hit.chunk_id)
                        if row is None or (
                            row["document_id"],
                            row["recipe_id"],
                            row["source_id"],
                            row["content_version"],
                            row["excerpt"],
                        ) != (hit.document_id, hit.recipe_id, hit.source_id,
                              hit.content_version, hit.excerpt):
                            raise RetrievalUnavailable("EVIDENCE_CHANGED")
                        ranked.append((row, float(hit.score)))
                else:
                    rows = evidence_rows(db, profile["generation_id"], document_type=document_type)
                    ranked = demo_hits(rows, query, embedding, limit)
                # Detect switches/revocation between SQL statements before returning evidence.
                if active_profile(db, settings, provider) != profile:
                    raise RetrievalUnavailable("INDEX_CHANGED")
                evidence = [
                    {
                        "id": (f"recipe:{row['recipe_id']}:v{row['content_version']}"
                               if row["document_type"] == "recipe" else
                               f"knowledge:{row['document_id']}:v{row['content_version']}"),
                        "kind": row["document_type"],
                        "document_id": row["document_id"],
                        "recipe_id": row["recipe_id"],
                        "title": row["title"],
                        "content_version": row["content_version"],
                        "chunk_id": row["chunk_id"],
                        "source_id": row["source_id"],
                        "source_url": row["url"],
                        "excerpt": row["excerpt"],
                        "content": row["content"],
                        "capabilities": row["capabilities"],
                        "score": score,
                    }
                    for row, score in ranked
                ]
            return ToolResult(
                status="ok" if evidence else "no_result",
                data={"evidence": evidence, "mode": settings.retrieval_mode, "fake": provider.fake},
                evidence_ids=[item["id"] for item in evidence],
                data_versions={
                    "profile_id": profile["profile_id"],
                    "generation_id": profile["generation_id"],
                    "model": provider.model,
                    "dimensions": provider.dimensions,
                    "template_version": provider.template_version,
                    "tokenizer_version": provider.tokenizer_version,
                },
                warnings=["fake embedding 仅验证检索链路，不代表语义质量"] if provider.fake else [],
            )
    except RetrievalUnavailable as error:  # Preserve the structured retrieval failure code.
        return ToolResult(
            status="unavailable",
            error_code=error.code,
            data={"mode": settings.retrieval_mode, "fake": provider.fake},
        )
    except Exception:
        # Never fall back to a different backend/profile/provider or turn failure into no_result.
        return ToolResult(
            status="unavailable",
            error_code="RETRIEVAL_FAILED",
            data={"mode": settings.retrieval_mode, "fake": provider.fake},
        )
