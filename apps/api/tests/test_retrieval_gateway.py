"""Gateway contract and real PG+vector integration, with injected database bindings."""

from dataclasses import replace

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker
from test_native_postgres import native_pg  # noqa: F401
from test_runtime_postgres_concurrency import postgres_runtime  # noqa: F401

from app.core.config import Settings
from app.db.models import Recipe
from app.db.session import SessionLocal
from app.db.v2_models import (
    EmbeddingProfile,
    IndexGeneration,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeSource,
)
from app.services import retrieval_gateway as gateway
from app.services.hybrid_retrieval import fake_embedding
from app.services.native_index_worker import rebuild_generation


def options(**kwargs):
    return Settings(_env_file=None, **kwargs)


def seed_demo():
    with SessionLocal.begin() as session:
        recipe = session.scalar(select(Recipe))
        session.add_all(
            [
                EmbeddingProfile(
                    id="demo-profile",
                    model="fake",
                    dimensions=64,
                    template_version="v1",
                    tokenizer_version="v1",
                    is_fake=True,
                ),
                KnowledgeSource(
                    id="demo-source", name="approved", license_note="test", approval="approved"
                ),
            ]
        )
        session.flush()
        session.add(
            IndexGeneration(
                id="demo-generation", profile_id="demo-profile", active=True, status="ready"
            )
        )
        session.add(
            KnowledgeDocument(
                id="demo-document",
                recipe_id=recipe.id,
                source_id="demo-source",
                content_version=2,
                published=True,
                content={"name": "番茄鸡蛋"},
                capabilities={},
                search_text="番茄鸡蛋",
            )
        )
        session.flush()
        session.add(
            KnowledgeChunk(
                id="real-chunk",
                document_id="demo-document",
                content_version=2,
                generation_id="demo-generation",
                ordinal=0,
                text="番茄鸡蛋原文",
                embedding=fake_embedding("番茄鸡蛋"),
            )
        )


def test_demo_explicit_fake_coherent_evidence_and_revocation():
    seed_demo()
    result = gateway.search("番茄鸡蛋", sessions=SessionLocal, settings=options())
    assert result.status == "ok"
    assert result.data["fake"] and result.data["mode"] == "demo"
    assert result.warnings
    evidence = result.data["evidence"][0]
    assert (
        evidence["chunk_id"],
        evidence["source_id"],
        evidence["content_version"],
        evidence["excerpt"],
    ) == ("real-chunk", "demo-source", 2, "番茄鸡蛋原文")
    assert result.evidence_ids == [evidence["id"]]
    with SessionLocal.begin() as session:
        session.get(KnowledgeDocument, "demo-document").content_version = 3
    assert (
        gateway.search("番茄鸡蛋", sessions=SessionLocal, settings=options()).status == "no_result"
    )
    with SessionLocal.begin() as session:
        session.get(KnowledgeDocument, "demo-document").content_version = 2
        session.get(KnowledgeSource, "demo-source").approval = "revoked"
    assert (
        gateway.search("番茄鸡蛋", sessions=SessionLocal, settings=options()).status == "no_result"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("model", "real-model"),
        ("dimensions", 32),
        ("template_version", "v2"),
        ("tokenizer_version", "v2"),
        ("is_fake", False),
    ],
)
def test_default_fake_rejects_every_profile_identity_mismatch(field, value):
    seed_demo()
    with SessionLocal.begin() as session:
        setattr(session.get(EmbeddingProfile, "demo-profile"), field, value)
    result = gateway.search("番茄", sessions=SessionLocal, settings=options())
    assert result.status == "unavailable"
    assert result.error_code == "EMBEDDING_PROFILE_MISMATCH"


def test_configuration_and_provider_must_both_match_before_embedding():
    seed_demo()

    def forbidden(_):
        pytest.fail("Profile mismatch must be rejected before provider invocation")

    provider = replace(gateway.FAKE_EMBEDDER, embed_query=forbidden, template_version="v2")
    result = gateway.search("番茄", sessions=SessionLocal, settings=options(), provider=provider)
    assert result.error_code == "EMBEDDING_PROFILE_MISMATCH"
    result = gateway.search(
        "番茄", sessions=SessionLocal, settings=options(retrieval_embedding_dimensions=32)
    )
    assert result.error_code == "EMBEDDING_PROFILE_MISMATCH"


def test_no_index_and_native_retrieval_do_not_fallback():
    assert (
        gateway.search("番茄", sessions=SessionLocal, settings=options()).error_code
        == "NO_ACTIVE_INDEX"
    )
    seed_demo()
    result = gateway.search(
        "番茄", sessions=SessionLocal, settings=options(retrieval_mode="native")
    )
    assert result.status == "unavailable" and result.error_code == "NATIVE_VECTOR_UNAVAILABLE"
    with pytest.raises(ValueError):
        options(retrieval_mode="auto")


def test_real_profile_requires_matching_settings_and_provider():
    seed_demo()
    with SessionLocal.begin() as session:
        profile = session.get(EmbeddingProfile, "demo-profile")
        profile.model, profile.is_fake = "approved-real-model", False
    result = gateway.search("番茄", sessions=SessionLocal,
        settings=options(retrieval_embedding_model="other-real-model"))
    assert result.error_code == "EMBEDDING_PROFILE_MISMATCH"
    result = gateway.search("番茄", sessions=SessionLocal,
        settings=options(retrieval_embedding_model="approved-real-model"))
    assert result.error_code == "DEMO_REQUIRES_FAKE_PROVIDER"
    provider = replace(gateway.FAKE_EMBEDDER, model="approved-real-model", fake=False,
                       embed_query=lambda query: fake_embedding(query))
    result = gateway.search("番茄", sessions=SessionLocal, provider=provider,
        settings=options(retrieval_embedding_model="approved-real-model"))
    assert result.error_code == "DEMO_REQUIRES_FAKE_PROVIDER"


@pytest.mark.parametrize("embedding", [[1.0], [float("nan")] * 64, [True] * 64])
def test_invalid_embedding_never_becomes_empty_success(embedding):
    seed_demo()
    provider = replace(gateway.FAKE_EMBEDDER, embed_query=lambda _: embedding)
    result = gateway.search("番茄", sessions=SessionLocal, settings=options(), provider=provider)
    assert result.status == "unavailable"


def test_provider_runs_outside_transaction_and_index_rechecked():
    seed_demo()
    with SessionLocal.kw["bind"].connect() as connection:

        def switched(_):
            assert not connection.in_transaction()
            with connection.begin():
                connection.execute(IndexGeneration.__table__.update().values(active=False))
            return fake_embedding("番茄")

        result = gateway.search(
            "番茄",
            connection=connection,
            settings=options(),
            provider=replace(gateway.FAKE_EMBEDDER, embed_query=switched),
        )
        assert result.status == "unavailable"
        assert result.error_code in {"NO_ACTIVE_INDEX", "INDEX_CHANGED"}


def test_native_gateway_uses_injected_database_and_real_chunk(native_pg):  # noqa: F811
    with native_pg.connect() as connection:
        generation_id = rebuild_generation(connection, profile_id="profile")
    sessions = sessionmaker(bind=native_pg)
    result = gateway.search("番茄", sessions=sessions, settings=options(retrieval_mode="native"))
    assert result.status == "ok" and result.data["mode"] == "native"
    assert result.data["fake"]
    assert result.data_versions["generation_id"] == generation_id
    assert result.data_versions["profile_id"] == "profile"
    with native_pg.connect() as connection:
        for evidence in result.data["evidence"]:
            row = connection.execute(
                text(
                    "SELECT c.text, d.recipe_id, d.content_version, s.id FROM knowledge_chunks c "
                    "JOIN knowledge_documents d ON d.id = c.document_id "
                    "JOIN knowledge_sources s ON s.id = d.source_id WHERE c.id = :id"
                ),
                {"id": evidence["chunk_id"]},
            ).one()
            assert tuple(row) == (
                evidence["excerpt"],
                evidence["recipe_id"],
                evidence["content_version"],
                evidence["source_id"],
            )
    # The global application DB remains unrelated and has no active index.
    assert (
        gateway.search("番茄", sessions=SessionLocal, settings=options()).error_code
        == "NO_ACTIVE_INDEX"
    )
    with native_pg.begin() as connection:
        connection.execute(KnowledgeSource.__table__.update().values(approval="revoked"))
    assert (
        gateway.search("番茄", sessions=sessions, settings=options(retrieval_mode="native")).status
        == "no_result"
    )


def test_native_provider_is_outside_transaction_and_profile_exact(native_pg):  # noqa: F811
    with native_pg.connect() as connection:
        rebuild_generation(connection, profile_id="profile")

        def embed(query):
            assert not connection.in_transaction()
            return fake_embedding(query)

        result = gateway.search(
            "番茄",
            connection=connection,
            settings=options(retrieval_mode="native"),
            provider=replace(gateway.FAKE_EMBEDDER, embed_query=embed),
        )
        assert result.status == "ok"
        with connection.begin():
            connection.execute(EmbeddingProfile.__table__.update().values(template_version="v2"))
        result = gateway.search(
            "番茄", connection=connection, settings=options(retrieval_mode="native")
        )
        assert result.error_code == "EMBEDDING_PROFILE_MISMATCH"


def test_native_pg_without_vector_returns_unavailable(postgres_runtime):  # noqa: F811
    engine, sessions, _actor = postgres_runtime
    with engine.begin() as connection:
        if connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector')")
        ):
            pytest.skip("This fault test requires a PostgreSQL DB without vector")
    with sessions.begin() as session:
        session.add(
            EmbeddingProfile(
                id="no-vector",
                model="fake",
                dimensions=64,
                template_version="v1",
                tokenizer_version="v1",
                is_fake=True,
            )
        )
        session.flush()
        session.add(IndexGeneration(profile_id="no-vector", active=True, status="ready"))
    result = gateway.search("番茄", sessions=sessions, settings=options(retrieval_mode="native"))
    assert result.status == "unavailable" and result.error_code == "NATIVE_VECTOR_UNAVAILABLE"
