"""Embed research chunks into recipe_research with local BGE.

Run from apps/api after the staging migration:
  python -m app.cli.embed_research_recipes --model-dir ../../models/bge-small-zh-v1.5

The command writes only the research schema in mealflow_s7. It leaves business
recipes and the 64-d knowledge index unchanged.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.cli.register_recipe_sources import ROOT, fingerprint
from app.core.config import get_settings
from app.services.research_index import (
    BgeEmbedder,
    batched,
    existing_chunk_ids,
    find_ready_batch,
    find_resumable_batch,
    insert_batch,
    insert_chunk_vectors,
    make_engine,
    mark_batch,
    model_profile,
    scalar,
    upsert_parents,
    validate_batch,
)
from app.services.research_recipes import load_jsonl

DEFAULT_PARENTS = ROOT / "output/howtocook-research-v1/parents.jsonl"
DEFAULT_CHUNKS = ROOT / "output/recipe-research-chunks-v1/chunks.jsonl"
DEFAULT_MODEL = ROOT / "models/bge-small-zh-v1.5"


def _policy(chunks_path: Path) -> str:
    summary_path = chunks_path.parent / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return str(summary["policy_version"])


def embed_files(
    *,
    parents_path: Path,
    chunks_path: Path,
    model_dir: Path,
    batch_size: int,
) -> dict[str, Any]:
    parents = load_jsonl(parents_path)
    chunks = load_jsonl(chunks_path)
    if not chunks:
        raise ValueError("没有可写入的切块")
    profile = model_profile(model_dir)
    corpus_sha, _size = fingerprint(parents_path)
    chunks_sha, _size = fingerprint(chunks_path)
    policy = _policy(chunks_path)
    engine = make_engine(get_settings().database_url)
    with engine.connect() as connection:
        if scalar(connection, "SELECT to_regclass('recipe_research.chunks')") is None:
            raise RuntimeError("recipe_research 尚未迁移，已停止")
        recipe_count = int(scalar(connection, "SELECT COUNT(*) FROM recipes"))
    with engine.begin() as connection:
        ready = find_ready_batch(connection, profile, corpus_sha, chunks_sha)
        stored = 0
        if ready is not None:
            stored = int(
                scalar(
                    connection,
                    "SELECT COUNT(*) FROM recipe_research.chunks WHERE batch_id = :id",
                    {"id": ready},
                )
            )
        if ready and stored == len(chunks):
            return {
                "batch_id": ready,
                "status": "ready_for_evaluation",
                "chunk_count": stored,
                "resumed": True,
                "recipe_count": recipe_count,
                "database_write_performed": False,
                "production_publishable": False,
            }
        batch_id = find_resumable_batch(connection, profile, corpus_sha, chunks_sha)
        if batch_id is None:
            batch_id = str(uuid4())
            insert_batch(connection, batch_id, profile, corpus_sha, chunks_sha, policy)
        else:
            mark_batch(connection, batch_id, status="building", error=None, counts=(0, 0))
        for group in batched(parents, 100):
            upsert_parents(connection, group)
    embedder = BgeEmbedder(model_dir)
    with engine.connect() as connection:
        done = existing_chunk_ids(connection, batch_id)
    pending = [chunk for chunk in chunks if chunk["chunk_id"] not in done]
    try:
        written = len(done)
        for group in batched(pending, batch_size):
            vectors = embedder.embed_documents([chunk["text"] for chunk in group])
            with engine.begin() as connection:
                insert_chunk_vectors(connection, batch_id, group, vectors)
            written += len(group)
            print(f"embedded {written}/{len(chunks)}", file=sys.stderr)
        with engine.begin() as connection:
            validate_batch(connection, batch_id, len(chunks))
            parent_count = len({chunk["document_id"] for chunk in chunks})
            mark_batch(
                connection,
                batch_id,
                status="ready_for_evaluation",
                error=None,
                counts=(len(chunks), parent_count),
            )
    except Exception as exc:
        with engine.begin() as connection:
            mark_batch(connection, batch_id, status="failed", error=str(exc), counts=(0, 0))
        raise
    return {
        "batch_id": batch_id,
        "status": "ready_for_evaluation",
        "chunk_count": len(chunks),
        "parent_count": len({chunk["document_id"] for chunk in chunks}),
        "model": profile,
        "recipe_count": recipe_count,
        "database_write_performed": True,
        "production_publishable": False,
        "quality_claim": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parents", type=Path, default=DEFAULT_PARENTS)
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    try:
        summary = embed_files(
            parents_path=args.parents if args.parents.is_absolute() else ROOT / args.parents,
            chunks_path=args.chunks if args.chunks.is_absolute() else ROOT / args.chunks,
            model_dir=args.model_dir if args.model_dir.is_absolute() else ROOT / args.model_dir,
            batch_size=args.batch_size,
        )
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"research embedding failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
