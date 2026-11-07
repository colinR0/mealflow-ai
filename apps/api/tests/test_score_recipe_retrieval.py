"""File-only retrieval baseline tests; no database connection is required."""

from __future__ import annotations

import json
from pathlib import Path

from app.cli.score_recipe_retrieval import (
    DEFAULT_CHUNKS,
    DEFAULT_QUESTIONS,
    INTENTS,
    evaluate,
    rank_documents,
    write_report,
)

EGG = "417fb370-8768-5723-b4e7-472cd5c9b164"
GONGBAO = "edae986a-b690-52ae-8bcf-115904eeca9c"
CHICKEN = "43ab3ba6-4f81-52d1-932e-e779b288e98a"
FISH = "rload-001"


def _chunk(document_id: str, name: str, section: str, text: str) -> dict:
    return {
        "chunk_id": f"{document_id}-{section}",
        "document_id": document_id,
        "name": name,
        "section": section,
        "text": text,
    }


def _corpus() -> list[dict]:
    return [
        _chunk(
            EGG,
            "番茄炒蛋",
            "full",
            "菜名：番茄炒蛋\n食材：\n- 番茄 1个\n- 鸡蛋 2个\n做法：\n1. 炒匀",
        ),
        _chunk(
            GONGBAO,
            "宫保鸡丁",
            "full",
            "菜名：宫保鸡丁\n食材：\n- 花生米 1份\n- 鸡胸肉 1份",
        ),
        _chunk(CHICKEN, "土豆烧鸡", "full", "菜名：土豆烧鸡\n食材：\n- 鸡腿 1只\n- 土豆 1个"),
        _chunk(
            FISH,
            "姜葱清蒸鲈鱼",
            "overview",
            "菜名：姜葱清蒸鲈鱼\n设备：蒸锅\n食材：\n- 鲈鱼 1条",
        ),
        _chunk(FISH, "姜葱清蒸鲈鱼", "steps", "菜名：姜葱清蒸鲈鱼\n做法：\n1. 蒸至鱼肉不透明"),
    ]


def _question(**overrides) -> dict:
    question = {
        "question_id": "q1",
        "query": "番茄炒蛋怎么做",
        "intent": "问做法",
        "category": "精确菜名",
        "answerable": True,
        "acceptable_document_ids": [EGG],
        "must_exclude_document_ids": [],
        "expected_sections": ["full"],
        "relevance": {EGG: 3},
    }
    question.update(overrides)
    return question


def test_exact_name_ranks_parent_once_and_prefers_whole_recipe():
    ranked = rank_documents("番茄炒蛋怎么做", _corpus(), ["番茄炒蛋", "鸡蛋", "番茄"])
    assert ranked[0]["document_id"] == EGG
    assert ranked[0]["section"] == "full"
    assert [item["document_id"] for item in ranked].count(EGG) == 1


def test_alias_and_negation_do_not_get_special_handling():
    chunks = _corpus()
    terms = ["番茄炒蛋", "宫保鸡丁", "土豆烧鸡", "花生米", "鸡胸肉", "鸡腿", "鸡蛋", "番茄"]
    assert rank_documents("宫爆鸡丁", chunks, terms) == []
    peanut = rank_documents("不要花生米的鸡肉菜", chunks, terms)
    assert [item["document_id"] for item in peanut] == [GONGBAO]
    summary, results = evaluate(
        [
            _question(
                question_id="neg",
                query="不要花生米的鸡肉菜",
                intent="按条件找菜",
                category="否定",
                acceptable_document_ids=[CHICKEN],
                must_exclude_document_ids=[GONGBAO],
                expected_sections=["full"],
                relevance={CHICKEN: 3},
            )
        ],
        chunks,
    )
    assert results[0]["hit_at_5"] is False
    assert results[0]["exclusion_violation"] is True
    assert summary["quality_claim"] is False
    assert summary["embedding_performed"] is False


def test_unanswerable_query_with_no_overlap_is_not_a_hit(tmp_path: Path):
    questions = [
        _question(
            question_id="none",
            query="提拉米苏",
            intent="查菜",
            category="无答案",
            answerable=False,
            acceptable_document_ids=[],
            expected_sections=[],
            relevance={},
        )
    ]
    summary, results = evaluate(questions, _corpus())
    assert results[0]["false_positive"] is False
    assert results[0]["top"] == []
    questions_path = tmp_path / "questions.jsonl"
    chunks_path = tmp_path / "chunks.jsonl"
    questions_path.write_text(json.dumps(questions[0], ensure_ascii=False) + "\n", encoding="utf-8")
    chunks_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in _corpus()),
        encoding="utf-8",
    )
    output_dir = tmp_path / "out"
    write_report(questions_path, chunks_path, output_dir, summary, results)
    written = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert written["database_write_performed"] is False
    assert written["questions_sha256"]


def test_development_set_covers_three_intents_and_real_chunk_ids():
    questions = [
        json.loads(line)
        for line in DEFAULT_QUESTIONS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    chunks = [
        json.loads(line)
        for line in DEFAULT_CHUNKS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    summary, _results = evaluate(questions, chunks)
    assert summary["question_count"] == 40
    assert set(summary["by_intent"]) == INTENTS
    assert all(bucket["questions"] > 0 for bucket in summary["by_intent"].values())
    assert summary["by_category"]["无答案"]["questions"] >= 4
    assert summary["by_category"]["否定"]["questions"] >= 3
    assert summary["by_category"]["别名"]["questions"] >= 3
