"""Score development retrieval questions against recipe chunks with string overlap.

Run from apps/api:
  python -m app.cli.score_recipe_retrieval

The score is a repeatable baseline. It does not embed, write PostgreSQL, or
stand in for retrieval quality. Negation is intentionally ignored so questions
such as “不要花生” show whether the matched ingredient was the one to exclude.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.cli.register_recipe_sources import ROOT, fingerprint

INTENTS = {"查菜", "问做法", "按条件找菜"}
CATEGORIES = {"精确菜名", "别名", "模糊需求", "食材组合", "步骤细节", "否定", "无答案"}
SECTIONS = {"full", "overview", "steps"}
SECTION_RANK = {"full": 0, "overview": 1, "steps": 2}
HIT_K = 5
MRR_K = 10
DEFAULT_QUESTIONS = ROOT / "data/eval/recipe_retrieval_dev_v1.jsonl"
DEFAULT_CHUNKS = ROOT / "output/recipe-chunks-v1/chunks.jsonl"
DEFAULT_OUTPUT = ROOT / "output/recipe-retrieval-dev-v1"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name} 第 {line_number} 行不是 JSON") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path.name} 第 {line_number} 行不是对象")
            rows.append(row)
    if not rows:
        raise ValueError(f"{path.name} 没有记录")
    return rows


def validate_questions(questions: list[dict[str, Any]], document_ids: set[str]) -> None:
    seen: set[str] = set()
    for question in questions:
        question_id = question.get("question_id")
        if not isinstance(question_id, str) or not question_id or question_id in seen:
            raise ValueError(f"问题编号缺失或重复：{question_id}")
        seen.add(question_id)
        if question.get("intent") not in INTENTS:
            raise ValueError(f"{question_id} 的用途不在查菜、问做法、按条件找菜之中")
        if question.get("category") not in CATEGORIES:
            raise ValueError(f"{question_id} 的类别无法识别")
        if not isinstance(question.get("query"), str) or not question["query"].strip():
            raise ValueError(f"{question_id} 缺少问句")
        answerable = question.get("answerable")
        acceptable = question.get("acceptable_document_ids")
        excluded = question.get("must_exclude_document_ids")
        expected = question.get("expected_sections")
        relevance = question.get("relevance")
        if not isinstance(answerable, bool):
            raise ValueError(f"{question_id} 的 answerable 必须是布尔值")
        if not isinstance(acceptable, list) or not isinstance(excluded, list):
            raise ValueError(f"{question_id} 的菜谱名单必须是列表")
        if not isinstance(expected, list) or any(section not in SECTIONS for section in expected):
            raise ValueError(f"{question_id} 的证据区块必须是 full、overview 或 steps")
        if not isinstance(relevance, dict):
            raise ValueError(f"{question_id} 的相关性必须是对象")
        if answerable and not acceptable:
            raise ValueError(f"{question_id} 标记为可回答，但没有可接受菜谱")
        if not answerable and acceptable:
            raise ValueError(f"{question_id} 标记为不可回答，却给了可接受菜谱")
        if set(acceptable) & set(excluded):
            raise ValueError(f"{question_id} 的可接受菜谱和必须排除菜谱重叠")
        unknown = [item for item in [*acceptable, *excluded] if item not in document_ids]
        if unknown:
            raise ValueError(f"{question_id} 引用了切分结果中不存在的菜谱：{unknown[0]}")
        if set(relevance) != set(acceptable):
            raise ValueError(f"{question_id} 的相关性必须只覆盖可接受菜谱")
        if any(not isinstance(grade, int) or not 1 <= grade <= 3 for grade in relevance.values()):
            raise ValueError(f"{question_id} 的相关性必须是 1 到 3")


def build_terms(chunks: list[dict[str, Any]]) -> list[str]:
    terms: set[str] = set()
    for chunk in chunks:
        name = str(chunk["name"]).strip()
        if len(name) >= 2:
            terms.add(name)
        for line in str(chunk["text"]).splitlines():
            labels: list[str] = []
            minimum = 2
            if line.startswith("- "):
                labels = [line[2:].split()[0].strip()]
            elif line.startswith("主要食材："):
                labels = [part.strip() for part in line.removeprefix("主要食材：").split("、")]
            elif line.startswith("设备："):
                labels = [part.strip() for part in line.removeprefix("设备：").split("、")]
            elif line.startswith("类别："):
                labels = [line.removeprefix("类别：").strip()]
                minimum = 1
            terms.update(label for label in labels if len(label) >= minimum)
    return sorted(terms)


def score_chunk(query: str, chunk: dict[str, Any], terms: list[str]) -> int:
    text = str(chunk["text"])
    name = str(chunk["name"])
    score = 10 if name in query else 0
    compact = "".join(char for char in query if "\u4e00" <= char <= "\u9fff")
    if score == 0 and 2 <= len(compact) <= 8 and compact in name:
        score = 6
    score += sum(1 for term in terms if term in query and term in text)
    return score


def rank_documents(
    query: str, chunks: list[dict[str, Any]], terms: list[str]
) -> list[dict[str, Any]]:
    """Keep one chunk per parent. Equal scores prefer the whole-recipe block."""
    best: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        score = score_chunk(query, chunk, terms)
        if score <= 0:
            continue
        document_id = chunk["document_id"]
        current = best.get(document_id)
        if current is None or score > current["score"] or (
            score == current["score"]
            and SECTION_RANK[chunk["section"]] < SECTION_RANK[current["section"]]
        ):
            best[document_id] = {
                "document_id": document_id,
                "name": chunk["name"],
                "section": chunk["section"],
                "chunk_id": chunk["chunk_id"],
                "score": score,
            }
    return sorted(best.values(), key=lambda item: (-item["score"], item["document_id"]))


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def evaluate(
    questions: list[dict[str, Any]], chunks: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    document_ids = {chunk["document_id"] for chunk in chunks}
    validate_questions(questions, document_ids)
    terms = build_terms(chunks)
    results = []
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for question in questions:
        ranked = rank_documents(question["query"], chunks, terms)
        top_ids = [item["document_id"] for item in ranked]
        acceptable = set(question["acceptable_document_ids"])
        rank = next(
            (index for index, doc_id in enumerate(top_ids[:MRR_K], 1) if doc_id in acceptable),
            None,
        )
        chosen = next((item for item in ranked[:HIT_K] if item["document_id"] in acceptable), None)
        result = {
            "question_id": question["question_id"],
            "query": question["query"],
            "intent": question["intent"],
            "category": question["category"],
            "answerable": question["answerable"],
            "rank": rank,
            "hit_at_5": bool(question["answerable"] and rank is not None and rank <= HIT_K),
            "reciprocal_rank": round(1 / rank, 4) if rank is not None else 0,
            "evidence_section": chosen["section"] if chosen else None,
            "evidence_ok": bool(chosen and chosen["section"] in question["expected_sections"]),
            "exclusion_violation": any(
                doc_id in set(question["must_exclude_document_ids"]) for doc_id in top_ids[:HIT_K]
            ),
            "false_positive": not question["answerable"] and bool(ranked),
            "top": ranked[:HIT_K],
        }
        results.append(result)
        groups[("intent", question["intent"])].append(result)
        groups[("category", question["category"])].append(result)

    def pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
        answerable = [row for row in rows if row["answerable"]]
        return {
            "questions": len(rows),
            "answerable": len(answerable),
            "hit_at_5": _rate(sum(row["hit_at_5"] for row in answerable), len(answerable)),
            "mrr_at_10": _mean([row["reciprocal_rank"] for row in answerable]),
            "evidence_section_rate": _rate(
                sum(row["evidence_ok"] for row in answerable), len(answerable)
            ),
            "exclusion_violations": sum(row["exclusion_violation"] for row in rows),
            "unanswerable_false_positives": sum(row["false_positive"] for row in rows),
        }

    answerable_rows = [row for row in results if row["answerable"]]
    summary = {
        "method": "string-overlap-v1",
        "question_count": len(results),
        "answerable_count": len(answerable_rows),
        "hit_at_5": _rate(sum(row["hit_at_5"] for row in answerable_rows), len(answerable_rows)),
        "mrr_at_10": _mean([row["reciprocal_rank"] for row in answerable_rows]),
        "evidence_section_rate": _rate(
            sum(row["evidence_ok"] for row in answerable_rows), len(answerable_rows)
        ),
        "exclusion_violations": sum(row["exclusion_violation"] for row in results),
        "unanswerable_false_positives": sum(row["false_positive"] for row in results),
        "by_intent": {name: pack(groups[("intent", name)]) for name in sorted(INTENTS)},
        "by_category": {
            name: pack(groups[("category", name)])
            for name in sorted(CATEGORIES)
            if ("category", name) in groups
        },
        "embedding_performed": False,
        "database_write_performed": False,
        "quality_claim": False,
    }
    return summary, results


def write_report(
    questions_path: Path,
    chunks_path: Path,
    output_dir: Path,
    summary: dict[str, Any],
    results: list[dict[str, Any]],
) -> None:
    questions_sha, _ = fingerprint(questions_path)
    chunks_sha, _ = fingerprint(chunks_path)
    summary = {
        **summary,
        "questions_sha256": questions_sha,
        "chunks_sha256": chunks_sha,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir, prefix=".tmp-score-") as tmp:
        tmp_dir = Path(tmp)
        (tmp_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (tmp_dir / "results.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results),
            encoding="utf-8",
        )
        for name in ("summary.json", "results.jsonl"):
            os.replace(tmp_dir / name, output_dir / name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    questions_path = args.questions if args.questions.is_absolute() else ROOT / args.questions
    chunks_path = args.chunks if args.chunks.is_absolute() else ROOT / args.chunks
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    try:
        questions = load_jsonl(questions_path)
        chunks = load_jsonl(chunks_path)
        summary, results = evaluate(questions, chunks)
        write_report(questions_path, chunks_path, output_dir, summary, results)
    except (OSError, ValueError, KeyError) as exc:
        print(f"recipe retrieval scoring failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
