"""Rebuild the active native generation with the configured embedding provider.

Run after changing chunking, import content, or the embedding profile:

    python scripts/rebuild_rag_index.py

The previous generation is retained; the new one is published only if the whole
build succeeds.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.services.native_index_worker import enqueue_rebuild, rebuild_generation
from app.services.retrieval_gateway import configured_embedder, document_embedder
from sqlalchemy import text


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild or enqueue the native RAG index.")
    parser.add_argument(
        "--enqueue", action="store_true",
        help="queue a knowledge_rebuild job instead of building in this process",
    )
    args = parser.parse_args()
    provider = configured_embedder(get_settings())
    with engine.connect() as connection:
        profile = connection.execute(text(
            "SELECT p.id, p.model, p.dimensions, p.template_version, p.tokenizer_version, "
            "p.is_fake FROM index_generations g "
            "JOIN embedding_profiles p ON p.id = g.profile_id "
            "WHERE g.active AND g.status = 'ready'"
        )).mappings().first()
        if profile is None:
            raise SystemExit("no active ready generation")
        identity = (
            provider.model,
            provider.dimensions,
            provider.template_version,
            provider.tokenizer_version,
        )
        indexed = (
            profile["model"],
            profile["dimensions"],
            profile["template_version"],
            profile["tokenizer_version"],
        )
        if identity != indexed or provider.fake != profile["is_fake"]:
            raise SystemExit(
                f"configured provider {identity} does not match active profile {indexed}"
            )
        if args.enqueue:
            with SessionLocal() as session:
                job_id = enqueue_rebuild(session, profile["id"])
                session.commit()
            print(json.dumps(
                {"job_id": job_id, "profile_id": profile["id"]}, ensure_ascii=False
            ))
            return
        connection.rollback()
        generation = rebuild_generation(
            connection,
            profile_id=profile["id"],
            embed=document_embedder(provider),
            fake=provider.fake,
            model=provider.model,
            count_tokens=provider.count_tokens,
        )
    print(json.dumps(
        {"generation_id": generation, "profile_id": profile["id"]}, ensure_ascii=False
    ))


if __name__ == "__main__":
    main()