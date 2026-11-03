"""Opt in with MEALFLOW_TEST_NATIVE_POSTGRES_URL; uses only a temporary schema."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.base import Base
from app.db.models import Recipe
from app.db.v2_models import (
    BackgroundJob,
    EmbeddingProfile,
    IndexGeneration,
    KnowledgeDocument,
    KnowledgeSource,
)
from app.services.hybrid_retrieval import fake_embedding
from app.services.native_index_worker import process_index_job, rebuild_generation
from app.services.native_retrieval import NativeRetriever, install_native_schema


@pytest.fixture
def native_pg():
    url = os.environ.get("MEALFLOW_TEST_NATIVE_POSTGRES_URL")
    if not url:
        pytest.skip("Set MEALFLOW_TEST_NATIVE_POSTGRES_URL to opt into real PG+vector tests")
    admin = create_engine(url, connect_args={"connect_timeout": 5})
    schema = "test_s5_" + uuid4().hex
    engine = None
    created = False
    try:
        if admin.dialect.name != "postgresql":
            pytest.fail("MEALFLOW_TEST_NATIVE_POSTGRES_URL must use PostgreSQL")
        with admin.begin() as connection:
            available = connection.scalar(text(
                "SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'"
            ))
            if not available:
                pytest.skip("Real PostgreSQL has no vector extension; native tests NOT verified")
            # Install into public so dropping the test schema cannot remove the extension.
            connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
            connection.execute(CreateSchema(schema))
            created = True
        engine = create_engine(url, connect_args={"connect_timeout": 5})

        @event.listens_for(engine, "connect")
        def set_search_path(dbapi_connection, _record):
            old = dbapi_connection.autocommit
            dbapi_connection.autocommit = True
            with dbapi_connection.cursor() as cursor:
                cursor.execute(f'SET search_path TO "{schema}", public')
            dbapi_connection.autocommit = old

        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            install_native_schema(connection, dimensions=64)
        with Session(engine) as session, session.begin():
            source = KnowledgeSource(id="source", name="fixture", approval="approved",
                                     license_note="fixture")
            profile = EmbeddingProfile(id="profile", model="fake", dimensions=64,
                                       template_version="v1", tokenizer_version="v1", is_fake=True)
            session.add_all([source, profile])
            for recipe_id in ("a", "b"):
                session.add(Recipe(id=recipe_id, name=recipe_id, servings=1, duration_minutes=1))
            session.flush()
            for recipe_id in ("a", "b"):
                session.add(KnowledgeDocument(
                    id="doc-" + recipe_id, recipe_id=recipe_id, source_id="source",
                    content_version=1, published=True, content={}, search_text="番茄 " + recipe_id,
                ))
            session.add(BackgroundJob(id="job", kind="knowledge_rebuild", business_id="profile",
                                      dedupe_key="s5", status="queued",
                                      next_run_at=datetime.now(UTC)))
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def test_native_rrf_best_parent_and_coherent_evidence(native_pg):
    with native_pg.connect() as connection:
        generation = rebuild_generation(connection, profile_id="profile")
        with connection.begin():
            indexes = connection.execute(text(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema() "
                "AND tablename = 'knowledge_chunks'"
            )).scalars().all()
            assert any("USING hnsw" in ddl and "vector_cosine_ops" in ddl for ddl in indexes)
            assert any("USING gin (search_tsv)" in ddl for ddl in indexes)
            # Equal rank scores/distance force deterministic chunk ranks a,a,b.
            connection.execute(text("DELETE FROM knowledge_chunks"))
            for chunk, doc, excerpt, ordinal in (
                ("a1", "doc-a", "zzz", 0), ("a2", "doc-a", "aaa", 1),
                ("b1", "doc-b", "bbb", 0),
            ):
                connection.execute(text("""
                    INSERT INTO knowledge_chunks
                      (id, document_id, content_version, generation_id, ordinal, text, embedding,
                       embedding_vector, search_tsv, created_at, updated_at)
                    VALUES (:id, :doc, 1, :g, :ord, :excerpt, '[]', CAST(:v AS vector),
                            to_tsvector('simple', 'tomato'), now(), now())
                """), {"id": chunk, "doc": doc, "g": generation, "ord": ordinal,
                        "excerpt": excerpt, "v": "[1," + ",".join(["0"] * 63) + "]"})
        hits = NativeRetriever(connection, profile_id="profile").search(
            "tomato", [1.0] + [0.0] * 63,
        )
        assert [hit.recipe_id for hit in hits] == ["a", "b"]
        assert float(hits[0].score) == pytest.approx(2 / 61)
        assert float(hits[1].score) == pytest.approx(2 / 63)
        assert (hits[0].chunk_id, hits[0].excerpt, hits[0].source_id) == ("a1", "zzz", "source")
        connection.rollback()
        with connection.begin():
            connection.execute(text(
                "UPDATE knowledge_documents SET content_version = 2 WHERE id = 'doc-a'"
            ))
        assert [hit.recipe_id for hit in NativeRetriever(connection, profile_id="profile").search(
            "tomato", [1.0] + [0.0] * 63,
        )] == ["b"]
        connection.rollback()
        with connection.begin():
            connection.execute(text("UPDATE knowledge_sources SET approval = 'revoked'"))
        assert NativeRetriever(connection, profile_id="profile").search(
            "tomato", [1.0] + [0.0] * 63,
        ) == []


def test_job_failure_retry_and_duplicate_result(native_pg):
    with native_pg.connect() as connection:
        previous = rebuild_generation(connection, profile_id="profile")

        def bad_provider(_content):
            assert not connection.in_transaction()
            raise RuntimeError("provider unavailable")

        with pytest.raises(RuntimeError, match="provider unavailable"):
            process_index_job(connection, "job", embed=bad_provider, model="fake")
        with connection.begin():
            assert connection.scalar(
                select(IndexGeneration.id).where(IndexGeneration.active)
            ) == previous
            assert connection.scalar(select(BackgroundJob.status)) == "failed"
        result = process_index_job(connection, "job")
        assert process_index_job(connection, "job") == result
        with connection.begin():
            assert connection.scalar(select(BackgroundJob.attempts)) == 2
            assert connection.scalar(
                select(IndexGeneration.id).where(IndexGeneration.active)
            ) == result


def test_concurrent_job_claim_and_build_lock(native_pg):
    entered, release = Event(), Event()

    def first():
        with native_pg.connect() as connection:
            def provider(content):
                assert not connection.in_transaction()
                entered.set()
                assert release.wait(10)
                return fake_embedding(content)
            return process_index_job(connection, "job", embed=provider, model="fake")

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(first)
        try:
            assert entered.wait(10)
            with native_pg.connect() as connection:
                with pytest.raises(RuntimeError, match="already running"):
                    process_index_job(connection, "job")
                with pytest.raises(RuntimeError, match="already running"):
                    rebuild_generation(connection, profile_id="profile")
        finally:
            release.set()
        result = pending.result(timeout=15)
    with native_pg.connect() as connection:
        assert process_index_job(connection, "job") == result


@pytest.mark.parametrize("kwargs", [
    {"fake": False}, {"model": "wrong-model"},
    {"embed": lambda _: [1.0], "model": "fake"},
    {"embed": lambda _: [float("nan")] * 64, "model": "fake"},
])
def test_profile_and_vector_mismatch_do_not_publish(native_pg, kwargs):
    with native_pg.connect() as connection:
        previous = rebuild_generation(connection, profile_id="profile")
        with pytest.raises(ValueError):
            rebuild_generation(connection, profile_id="profile", **kwargs)
        with connection.begin():
            assert connection.scalar(
                select(IndexGeneration.id).where(IndexGeneration.active)
            ) == previous


def test_profile_change_during_embedding_prevents_activation(native_pg):
    with native_pg.connect() as connection:
        previous = rebuild_generation(connection, profile_id="profile")

        def provider(content):
            with native_pg.begin() as other:
                other.execute(text(
                    "UPDATE embedding_profiles SET tokenizer_version = 'v2' WHERE id = 'profile'"
                ))
            return fake_embedding(content)

        with pytest.raises(ValueError, match="profile changed"):
            rebuild_generation(connection, profile_id="profile", embed=provider, model="fake")
        with connection.begin():
            assert connection.scalar(
                select(IndexGeneration.id).where(IndexGeneration.active)
            ) == previous


def test_cross_profile_concurrent_activation_is_serialized(native_pg):
    from threading import Barrier

    with native_pg.begin() as connection:
        connection.execute(text("""
            INSERT INTO embedding_profiles
              (id, model, dimensions, template_version, tokenizer_version, is_fake,
               created_at, updated_at)
            VALUES ('second', 'fake', 64, 'v1', 'v1', true, now(), now())
        """))
    barrier = Barrier(2)

    def build(profile_id):
        first_call = True
        with native_pg.connect() as connection:
            def provider(content):
                nonlocal first_call
                if first_call:
                    first_call = False
                    barrier.wait(timeout=10)
                return fake_embedding(content)
            return rebuild_generation(
                connection, profile_id=profile_id, embed=provider, model="fake",
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(build, profile_id) for profile_id in ("profile", "second")]
        generations = [future.result(timeout=20) for future in futures]
    with native_pg.begin() as connection:
        active = connection.execute(
            select(IndexGeneration.id).where(IndexGeneration.active)
        ).scalars().all()
        assert len(active) == 1
        assert active[0] in generations
        statuses = connection.execute(select(IndexGeneration.status)).scalars().all()
        assert statuses == ["ready", "ready"]


def test_abandoned_running_job_can_be_reclaimed(native_pg):
    with native_pg.begin() as connection:
        connection.execute(text(
            "UPDATE background_jobs SET status = 'running', attempts = 1, "
            "worker_id = 'disconnected-worker', lease_until = now() + interval '1 hour'"
        ))
    with native_pg.connect() as connection:
        generation = process_index_job(connection, "job")
        with connection.begin():
            job = connection.execute(select(BackgroundJob)).first()
            assert job.status == "succeeded"
            assert job.attempts == 2
            assert job.worker_id is None
            assert job.lease_until is None
            assert connection.scalar(
                select(IndexGeneration.id).where(IndexGeneration.active)
            ) == generation
