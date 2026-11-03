"""S5/S6 boundary: explicit retrieval mode and injected database/provider dependencies.

No alternate database and no fallback. A supplied Connection must be idle: this module
owns short read transactions and calls embedding outside them. Sessions supplies its own
bound connection (including the caller's schema mapping), never global SessionLocal.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

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


FAKE_EMBEDDER = QueryEmbedder(fake_embedding, "fake", 64, "v1", "v1", True)


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


def evidence_rows(connection, generation_id, chunk_ids=None):
    statement = (
        select(
            KnowledgeDocument.recipe_id,
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
    return connection.execute(statement).mappings().all()


def demo_hits(rows, query, embedding, limit):
    keyword, vector = [], []
    for row in rows:
        overlap = sum(term in row["excerpt"].lower() for term in query.lower().split())
        if overlap:
            keyword.append((overlap, row))
        similarity = cosine(embedding, row["embedding"])
        if similarity > 0:
            vector.append((similarity, row))
    channels = []
    best = {}
    for candidates in (keyword, vector):
        candidates.sort(key=lambda item: (-item[0], item[1]["recipe_id"], item[1]["chunk_id"]))
        hits = []
        for rank, (score, row) in enumerate(candidates[:40], 1):
            hits.append(
                RetrievalHit(row["recipe_id"], row["chunk_id"], rank, score, row["excerpt"])
            )
            choice = (rank, row["chunk_id"])
            if row["recipe_id"] not in best or choice < best[row["recipe_id"]][0]:
                best[row["recipe_id"]] = (choice, row)
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
) -> ToolResult:
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        return ToolResult(status="error", error_code="INVALID_QUERY")
    if type(limit) is not int or not 1 <= limit <= 100:
        return ToolResult(status="error", error_code="INVALID_LIMIT")
    settings, provider = settings or get_settings(), provider or FAKE_EMBEDDER
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
            # No DB transaction spans a possibly remote embedding request.
            embedding = provider.embed_query(query)
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
                    ).search(query, embedding, limit)
                    rows = evidence_rows(
                        db, profile["generation_id"], [hit.chunk_id for hit in hits]
                    )
                    by_chunk = {row["chunk_id"]: row for row in rows}
                    ranked = []
                    for hit in hits:
                        row = by_chunk.get(hit.chunk_id)
                        if row is None or (
                            row["recipe_id"],
                            row["source_id"],
                            row["content_version"],
                            row["excerpt"],
                        ) != (hit.recipe_id, hit.source_id, hit.content_version, hit.excerpt):
                            raise RetrievalUnavailable("EVIDENCE_CHANGED")
                        ranked.append((row, float(hit.score)))
                else:
                    rows = evidence_rows(db, profile["generation_id"])
                    ranked = demo_hits(rows, query, embedding, limit)
                # Detect switches/revocation between SQL statements before returning evidence.
                if active_profile(db, settings, provider) != profile:
                    raise RetrievalUnavailable("INDEX_CHANGED")
                evidence = [
                    {
                        "id": f"recipe:{row['recipe_id']}:v{row['content_version']}",
                        "kind": "recipe",
                        "recipe_id": row["recipe_id"],
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
    except RetrievalUnavailable as error:
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
