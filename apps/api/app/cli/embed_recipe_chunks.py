"""Embed homemade recipe chunks locally and compare cosine retrieval with string overlap.

Run from apps/api:
  python -m app.cli.embed_recipe_chunks --model-dir ..\\..\\models\\Qwen3-Embedding-0.6B

This does not call a cloud embedding API, write PostgreSQL, or publish recipes.
If the local model directory has no config.json, the command stops.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from app.cli.register_recipe_sources import ROOT, fingerprint
from app.cli.score_recipe_retrieval import (
    DEFAULT_CHUNKS,
    DEFAULT_QUESTIONS,
    build_terms,
    load_jsonl,
    rank_documents,
    validate_questions,
)
from app.services.recipe_embeddings import (
    QwenRecipeEmbedder,
    pilot_metrics,
    rank_by_cosine,
    require_model_dir,
)

DEFAULT_OUTPUT = ROOT / "output/recipe-embeddings-v1"
DEFAULT_MODEL = ROOT / "models/Qwen3-Embedding-0.6B"


def embed_corpus(
    chunks: list[dict[str, Any]], embedder: QwenRecipeEmbedder
) -> dict[str, list[float]]:
    vectors = embedder.embed_documents([chunk["text"] for chunk in chunks])
    if len(vectors) != len(chunks):
        raise ValueError("嵌入条数与切块条数不一致")
    return {chunk["chunk_id"]: vector for chunk, vector in zip(chunks, vectors, strict=True)}


def compare(
    questions: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    vectors: dict[str, list[float]],
    query_vectors: list[list[float]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    validate_questions(questions, {chunk["document_id"] for chunk in chunks})
    if len(query_vectors) != len(questions):
        raise ValueError("查询向量条数与问题条数不一致")
    terms = build_terms(chunks)
    vector_rankings: dict[str, list[str]] = {}
    string_rankings: dict[str, list[str]] = {}
    results = []
    for question, query_vector in zip(questions, query_vectors, strict=True):
        vector_top = rank_by_cosine(query_vector, chunks, vectors)
        string_top = rank_documents(question["query"], chunks, terms)
        vector_rankings[question["question_id"]] = [item["document_id"] for item in vector_top]
        string_rankings[question["question_id"]] = [item["document_id"] for item in string_top]
        results.append(
            {
                "question_id": question["question_id"],
                "query": question["query"],
                "intent": question["intent"],
                "vector_top": vector_top[:5],
                "string_top": string_top[:5],
            }
        )
    summary = {
        "scope": "Offline semantic pilot on the 47 chunks from 40 homemade recipes",
        "vector": {
            "method": "cosine-qwen3-embedding-0.6b",
            **pilot_metrics(questions, vector_rankings),
        },
        "string_baseline": {
            "method": "string-overlap-v1",
            **pilot_metrics(questions, string_rankings),
        },
        "embedding_performed": True,
        "database_write_performed": False,
        "production_publishable": False,
        "quality_claim": False,
    }
    return summary, results


def write_pilot(
    questions_path: Path,
    chunks_path: Path,
    output_dir: Path,
    *,
    chunks: list[dict[str, Any]],
    vectors: dict[str, list[float]],
    profile: dict[str, Any],
    summary: dict[str, Any],
    results: list[dict[str, Any]],
) -> None:
    questions_sha, _ = fingerprint(questions_path)
    chunks_sha, _ = fingerprint(chunks_path)
    summary = {
        **summary,
        "profile": profile,
        "questions_sha256": questions_sha,
        "chunks_sha256": chunks_sha,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir, prefix=".tmp-embed-") as tmp:
        tmp_dir = Path(tmp)
        lines = []
        for chunk in chunks:
            lines.append(
                json.dumps(
                    {
                        "chunk_id": chunk["chunk_id"],
                        "document_id": chunk["document_id"],
                        "content_version": chunk["content_version"],
                        "embedding": vectors[chunk["chunk_id"]],
                    },
                    ensure_ascii=False,
                )
            )
        (tmp_dir / "vectors.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (tmp_dir / "profile.json").write_text(
            json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (tmp_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (tmp_dir / "results.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results),
            encoding="utf-8",
        )
        for name in ("vectors.jsonl", "profile.json", "summary.json", "results.jsonl"):
            os.replace(tmp_dir / name, output_dir / name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    questions_path = args.questions if args.questions.is_absolute() else ROOT / args.questions
    chunks_path = args.chunks if args.chunks.is_absolute() else ROOT / args.chunks
    model_dir = args.model_dir if args.model_dir.is_absolute() else ROOT / args.model_dir
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    try:
        require_model_dir(model_dir)
        questions = load_jsonl(questions_path)
        chunks = load_jsonl(chunks_path)
        embedder = QwenRecipeEmbedder(model_dir)
        vectors = embed_corpus(chunks, embedder)
        query_vectors = embedder.embed_queries([question["query"] for question in questions])
        summary, results = compare(questions, chunks, vectors, query_vectors)
        write_pilot(
            questions_path,
            chunks_path,
            output_dir,
            chunks=chunks,
            vectors=vectors,
            profile=embedder.profile,
            summary=summary,
            results=results,
        )
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"recipe embedding pilot failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
