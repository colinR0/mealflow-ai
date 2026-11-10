"""Read-only research retrieval. It does not call the cooking agent.

Run from apps/api:
  python -m app.cli.search_research_recipes --batch-id <id> --query "番茄炒蛋"
  python -m app.cli.search_research_recipes --batch-id <id> --questions questions.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from app.cli.register_recipe_sources import ROOT
from app.core.config import get_settings
from app.services.research_index import (
    BgeEmbedder,
    make_engine,
    retrieval_metrics,
    search_parents,
)
from app.services.research_recipes import load_jsonl

DEFAULT_MODEL = ROOT / "models/bge-small-zh-v1.5"
DEFAULT_REPORT = ROOT / "output/recipe-research-eval-v1"


def search_one(
    *,
    batch_id: str,
    query: str,
    limit: int,
    model_dir: Path,
) -> dict[str, Any]:
    embedder = BgeEmbedder(model_dir)
    vector = embedder.embed_queries([query])[0]
    engine = make_engine(get_settings().database_url)
    with engine.begin() as connection:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        vector_hits = search_parents(
            connection,
            batch_id=batch_id,
            query_vector=vector,
            query=query,
            limit=limit,
            mode="vector",
        )
        fts_hits = search_parents(
            connection, batch_id=batch_id, query_vector=vector, query=query, limit=limit, mode="fts"
        )
    return {"query": query, "vector": _public_hits(vector_hits), "fts": _public_hits(fts_hits)}


def _public_hits(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits = []
    for row in rows:
        hits.append(
            {
                "document_id": row["document_id"],
                "name": row["name"],
                "section": row["section"],
                "chunk_id": row["chunk_id"],
                "cosine": round(float(row["cosine"]), 4),
                "fts_rank": round(float(row["fts_rank"]), 4),
                "source_asset_key": row["source_asset_key"],
                "source_sha256": row["source_sha256"],
                "source_record_key": row["source_record_key"],
                "content_version": row["content_version"],
                "policy_version": row["policy_version"],
                "model_name": row["model_name"],
                "batch_id": row["batch_id"],
            }
        )
    return hits


def evaluate(
    batch_id: str, question_paths: list[Path], model_dir: Path, report_dir: Path
) -> dict[str, Any]:
    questions = [question for path in question_paths for question in load_jsonl(path)]
    embedder = BgeEmbedder(model_dir)
    engine = make_engine(get_settings().database_url)
    vector_rankings: dict[str, list[str]] = {}
    fts_rankings: dict[str, list[str]] = {}
    latencies = []
    details = []
    for question in questions:
        started = time.perf_counter()
        vector = embedder.embed_queries([question["query"]])[0]
        with engine.begin() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            vector_hits = search_parents(
                connection,
                batch_id=batch_id,
                query_vector=vector,
                query=question["query"],
                limit=10,
                mode="vector",
            )
            fts_hits = search_parents(
                connection,
                batch_id=batch_id,
                query_vector=vector,
                query=question["query"],
                limit=10,
                mode="fts",
            )
        elapsed = time.perf_counter() - started
        latencies.append(elapsed)
        vector_rankings[question["question_id"]] = [row["document_id"] for row in vector_hits]
        fts_rankings[question["question_id"]] = [row["document_id"] for row in fts_hits]
        details.append(
            {
                "question_id": question["question_id"],
                "query": question["query"],
                "latency_ms": round(elapsed * 1000, 1),
                "vector": _public_hits(vector_hits[:5]),
                "fts": _public_hits(fts_hits[:5]),
            }
        )
    vector_metrics = retrieval_metrics(questions, vector_rankings)
    fts_metrics = retrieval_metrics(questions, fts_rankings)
    summary = {
        "batch_id": batch_id,
        "question_count": len(questions),
        "vector": vector_metrics,
        "fts": fts_metrics,
        "mean_latency_ms": round(sum(latencies) / len(latencies) * 1000, 1) if latencies else None,
        "allergen_hard_filter": False,
        "production_publishable": False,
        "quality_claim": False,
        "database_write_performed": False,
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (report_dir / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in details),
        encoding="utf-8",
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--query")
    parser.add_argument("--questions", type=Path, action="append", default=[])
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    if not args.query and not args.questions:
        parser.error("提供 --query 或 --questions")
    model_dir = args.model_dir if args.model_dir.is_absolute() else ROOT / args.model_dir
    try:
        if args.questions:
            summary = evaluate(args.batch_id, args.questions, model_dir, args.report_dir)
        else:
            summary = search_one(
                batch_id=args.batch_id, query=args.query, limit=args.limit, model_dir=model_dir
            )
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"research search failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
