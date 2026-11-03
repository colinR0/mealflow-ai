"""Versioned, approved-source retrieval for local integration (fake vectors only)."""

import re

from sqlalchemy import select

from app.db.session import SessionLocal
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

PROFILE = "local-fake-64-v1"


def search(query: str, limit: int = 8) -> dict:
    with SessionLocal() as session:
        generation = session.scalar(
            select(IndexGeneration).where(
                IndexGeneration.active.is_(True), IndexGeneration.status == "ready"
            )
        )
        if generation is None:
            return {
                "status": "unavailable",
                "evidence": [],
                "warnings": ["尚无已审核的活动知识索引"],
            }
        profile = session.get(EmbeddingProfile, generation.profile_id)
        if profile.id != PROFILE or not profile.is_fake or profile.dimensions != 64:
            return {
                "status": "unavailable",
                "evidence": [],
                "warnings": ["当前 Provider 不匹配索引 profile"],
            }
        rows = session.execute(
            select(KnowledgeDocument, KnowledgeChunk, KnowledgeSource)
            .join(KnowledgeChunk, KnowledgeChunk.document_id == KnowledgeDocument.id)
            .join(KnowledgeSource, KnowledgeSource.id == KnowledgeDocument.source_id)
            .where(
                KnowledgeDocument.published.is_(True),
                KnowledgeSource.approval == "approved",
                KnowledgeChunk.generation_id == generation.id,
                KnowledgeChunk.content_version == KnowledgeDocument.content_version,
            )
        ).all()
        terms = set(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]", query.lower()))
        query_vector = fake_embedding(query)
        keyword, vector, exact = [], [], []
        for document, chunk, _source in rows:
            overlap = sum(term in chunk.text.lower() for term in terms)
            if overlap:
                keyword.append((overlap, document.recipe_id, chunk))
            similarity = cosine(query_vector, chunk.embedding)
            if similarity > 0:
                vector.append((similarity, document.recipe_id, chunk))
            if query.strip() == document.content.get("name"):
                exact.append(document.recipe_id)
        channels = []
        for candidates in (keyword, vector):
            candidates.sort(key=lambda row: (-row[0], row[1], row[2].id))
            channels.append(
                [
                    RetrievalHit(recipe, chunk.id, rank, score, chunk.text)
                    for rank, (score, recipe, chunk) in enumerate(candidates[:40], 1)
                ]
            )
        ranked = list(
            dict.fromkeys(sorted(exact) + [item[0] for item in reciprocal_rank_fusion(channels)])
        )
        evidence = []
        for recipe_id in ranked[: min(8, max(1, limit))]:
            document, chunk, source = next(row for row in rows if row[0].recipe_id == recipe_id)
            evidence.append(
                {
                    "id": f"recipe:{recipe_id}:v{document.content_version}",
                    "kind": "recipe",
                    "recipe_id": recipe_id,
                    "source_id": source.id,
                    "content_version": document.content_version,
                    "chunk_id": chunk.id,
                    "excerpt": chunk.text,
                    "content": document.content,
                    "source_url": source.url,
                    "capabilities": document.capabilities,
                }
            )
        return {
            "status": "ok" if evidence else "no_result",
            "evidence": evidence,
            "profile_id": profile.id,
            "generation_id": generation.id,
            "fake": True,
            "warnings": ["fake embedding 仅用于本地集成，不代表语义检索质量"],
        }
