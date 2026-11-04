"""S5 native builds using short owned transactions and PostgreSQL session locks.

Pass an idle Connection (not engine.begin()). The session lock spans provider calls,
but no database transaction does. Never share this connection between threads.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Connection, text

from app.services.hybrid_retrieval import fake_embedding
from app.services.native_retrieval import tokenize_chinese, vector_literal

EmbeddingProvider = Callable[[str], Sequence[float]]


def _require_idle(connection: Connection) -> None:
    if connection.dialect.name != "postgresql":
        raise RuntimeError("S5 native index worker requires PostgreSQL")
    if connection.in_transaction():
        raise RuntimeError("native worker requires an idle connection; it owns short transactions")


@contextmanager
def _session_lock(connection: Connection, key: str):
    with connection.begin():
        acquired = connection.scalar(text(
            "SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"
        ), {"key": key})
    if not acquired:
        raise RuntimeError("native index build/job is already running")
    try:
        yield
    finally:
        if connection.in_transaction():
            connection.rollback()
        with connection.begin():
            connection.execute(text(
                "SELECT pg_advisory_unlock(hashtextextended(:key, 0))"
            ), {"key": key})


def _profile(connection: Connection, profile_id: str):
    row = connection.execute(text(
        "SELECT model, dimensions, is_fake, template_version, tokenizer_version "
        "FROM embedding_profiles WHERE id = :id FOR SHARE"
    ), {"id": profile_id}).first()
    if row is None:
        raise LookupError("embedding profile not found")
    return tuple(row)


def rebuild_generation(
    connection: Connection,
    *,
    profile_id: str,
    embed: EmbeddingProvider = fake_embedding,
    fake: bool = True,
    model: str | None = None,
) -> str:
    """Build and atomically publish, retaining the previous generation on failure.

    Real/custom providers must explicitly supply their model identity. A global
    activation lock matches the ORM's global unique active-generation constraint.
    """
    _require_idle(connection)
    with _session_lock(connection, "mealflow:s5:profile:" + profile_id):
        return _build(connection, profile_id, embed, fake, model)


def _build(connection, profile_id, embed, fake, model, job_id=None):
    generation_id = str(uuid4())
    with connection.begin():
        profile = _profile(connection, profile_id)
        provider_model = "fake" if embed is fake_embedding and model is None else model
        if provider_model != profile[0] or type(fake) is not bool or fake != profile[2]:
            raise ValueError("embedding provider model/fake flag does not match profile")
        if embed is fake_embedding and (not fake or profile[1] != 64 or provider_model != "fake"):
            raise ValueError("fake embedding requires a fake 64-dimensional profile")
        docs = connection.execute(text(
            "SELECT id, content_version, search_text FROM knowledge_documents ORDER BY id"
        )).all()
        connection.execute(text("""
            INSERT INTO index_generations (id, profile_id, active, status, created_at, updated_at)
            VALUES (:id, :profile, false, 'building', :now, :now)
        """), {"id": generation_id, "profile": profile_id, "now": datetime.now(UTC)})
    try:
        # Provider calls deliberately execute outside any database transaction.
        for doc_id, version, content in docs:
            content = content or ""
            embedding = list(embed(content))
            literal = vector_literal(embedding)
            if len(embedding) != profile[1]:
                raise ValueError("embedding dimension does not match profile")
            tokens = tokenize_chinese(content)
            with connection.begin():
                connection.execute(text("""
                    INSERT INTO knowledge_chunks
                      (id, document_id, content_version, generation_id, ordinal, text, embedding,
                       embedding_vector, search_tsv, created_at, updated_at)
                    VALUES (:id, :doc, :version, :generation, 0, :text, CAST(:embedding AS json),
                            CAST(:vector AS vector), to_tsvector('simple', :tokens), :now, :now)
                """), {
                    "id": str(uuid4()), "doc": doc_id, "version": version,
                    "generation": generation_id, "text": content, "embedding": literal,
                    "vector": literal,
                    "tokens": tokens, "now": datetime.now(UTC),
                })
        with connection.begin():
            connection.execute(text(
                "SELECT pg_advisory_xact_lock(hashtextextended('mealflow:s5:activate', 0))"
            ))
            if _profile(connection, profile_id) != profile:
                raise ValueError("embedding profile changed during build; rebuild required")
            connection.execute(text(
                "UPDATE index_generations SET active = false, updated_at = :now WHERE active"
            ), {"now": datetime.now(UTC)})
            connection.execute(text(
                "UPDATE index_generations SET active = true, status = 'ready', updated_at = :now "
                "WHERE id = :id"
            ), {"id": generation_id, "now": datetime.now(UTC)})
            if job_id is not None:
                connection.execute(text(
                    "UPDATE background_jobs SET status = 'succeeded', native_generation_id = :g, "
                    "worker_id = NULL, lease_until = NULL, updated_at = :now WHERE id = :id"
                ), {"id": job_id, "g": generation_id, "now": datetime.now(UTC)})
        return generation_id
    except BaseException:  # Mark the generation failed before propagating interruption or error.
        with connection.begin():
            connection.execute(text(
                "UPDATE index_generations SET status = 'failed', updated_at = :now WHERE id = :id"
            ), {"id": generation_id, "now": datetime.now(UTC)})
        raise


def process_index_job(
    connection: Connection, job_id: str, *, embed=fake_embedding, fake: bool = True,
    model: str | None = None,
) -> str:
    """Deduplicate by job lock; retry failed/crashed claims, return completed result.

    Session locks release on disconnect, so a crashed ``running`` claim is safely
    reclaimable without waiting for a lease. This adapter owns job claiming.
    """
    _require_idle(connection)
    with _session_lock(connection, "mealflow:s5:job:" + job_id):
        with connection.begin():
            job = connection.execute(text(
                "SELECT business_id, status, native_generation_id FROM background_jobs "
                "WHERE id = :id AND kind = 'knowledge_rebuild' FOR UPDATE"
            ), {"id": job_id}).first()
            if job is None:
                raise LookupError("knowledge rebuild job not found")
            if job.status == "succeeded":
                if not job.native_generation_id:
                    raise RuntimeError("completed native job has no generation result")
                return job.native_generation_id
            if job.status not in {"queued", "failed", "running"}:
                raise RuntimeError("knowledge rebuild job is not runnable")
            connection.execute(text(
                "UPDATE background_jobs SET status = 'running', attempts = attempts + 1, "
                "updated_at = :now WHERE id = :id"
            ), {"id": job_id, "now": datetime.now(UTC)})
        try:
            with _session_lock(connection, "mealflow:s5:profile:" + job.business_id):
                return _build(connection, job.business_id, embed, fake, model, job_id)
        except BaseException:  # Release the job lease and persist failure before re-raising.
            with connection.begin():
                connection.execute(text(
                    "UPDATE background_jobs SET status = 'failed', worker_id = NULL, "
                    "lease_until = NULL, updated_at = :now WHERE id = :id"
                ), {"id": job_id, "now": datetime.now(UTC)})
            raise
