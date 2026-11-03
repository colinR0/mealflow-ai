from sqlalchemy import select

from app.db.models import Recipe
from app.db.session import SessionLocal
from app.db.v2_models import (
    EmbeddingProfile,
    IndexGeneration,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeSource,
)
from app.services.hybrid_retrieval import fake_embedding
from app.services.knowledge_demo import PROFILE, search


def test_search_approved_current_published_and_revocation():
    with SessionLocal() as session, session.begin():
        recipe = session.scalar(select(Recipe))
        source = KnowledgeSource(name="自制演示", approval="approved", license_note="test fixture")
        profile = EmbeddingProfile(
            id=PROFILE,
            model="fake",
            dimensions=64,
            template_version="v1",
            tokenizer_version="v1",
            is_fake=True,
        )
        session.add_all([source, profile])
        session.flush()
        generation = IndexGeneration(profile_id=PROFILE, active=True, status="ready")
        session.add(generation)
        session.flush()
        document = KnowledgeDocument(
            recipe_id=recipe.id,
            source_id=source.id,
            published=True,
            content_version=1,
            capabilities={"has_steps": False},
            content={"name": "番茄演示", "steps": []},
            search_text="番茄演示",
        )
        session.add(document)
        session.flush()
        session.add(
            KnowledgeChunk(
                document_id=document.id,
                content_version=1,
                generation_id=generation.id,
                ordinal=0,
                text="番茄演示",
                embedding=fake_embedding("番茄演示"),
            )
        )
        document_id, source_id = document.id, source.id
    result = search("番茄演示")
    assert result["fake"] and len(result["evidence"]) == 1
    assert result["evidence"][0]["content"]["steps"] == []
    with SessionLocal() as session, session.begin():
        session.get(KnowledgeDocument, document_id).content_version = 2
    assert search("番茄演示")["evidence"] == []
    with SessionLocal() as session, session.begin():
        session.get(KnowledgeDocument, document_id).content_version = 1
        session.get(KnowledgeSource, source_id).approval = "revoked"
    assert search("番茄演示")["evidence"] == []
