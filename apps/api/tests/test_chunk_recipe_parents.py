"""File-only chunking tests; no database connection is required."""

from __future__ import annotations

import json
from pathlib import Path

from app.cli.chunk_recipe_parents import build_corpus, split_parent


def _parent() -> dict:
    return {
        "document_id": "recipe-1",
        "name": "番茄炒蛋",
        "status": "loaded",
        "source_asset_key": "data/seed/recipes.json",
        "source_sha256": "a" * 64,
        "parsed_sha256": None,
        "capabilities": {"chunkable": True, "publishable_candidate": True},
        "fields": {"servings": 2, "duration_minutes": 15, "allergens": ["蛋"]},
        "ingredients": [
            {"key": "tomato", "name": "tomato", "quantity": 0.3, "unit": "kg"},
            {"key": "egg", "name": "egg", "quantity": 3, "unit": "个"},
        ],
        "steps": [
            {"ordinal": 1, "text": "洗净番茄并切块。"},
            {"ordinal": 2, "text": "鸡蛋打散，炒至凝固。"},
            {"ordinal": 3, "text": "加入番茄炒出汁后混合。"},
        ],
    }


def test_short_recipe_stays_whole_and_uses_chinese_ingredient_names():
    parent = _parent()
    chunks = split_parent(parent, max_chars=450, policy_version="test-v1")
    assert len(chunks) == 1
    assert chunks[0]["section"] == "full"
    assert chunks[0]["step_ordinals"] == [1, 2, 3]
    assert "番茄 0.3kg" in chunks[0]["text"]
    assert "鸡蛋 3个" in chunks[0]["text"]
    assert chunks == split_parent(parent, max_chars=450, policy_version="test-v1")


def test_long_recipe_preserves_all_steps_without_splitting_one_step():
    parent = _parent()
    for step in parent["steps"]:
        step["text"] += "先检查食材是否熟透，再继续下一步。" * 5
    chunks = split_parent(parent, max_chars=300, policy_version="test-v1")
    assert chunks[0]["section"] == "overview"
    assert [n for chunk in chunks for n in chunk["step_ordinals"]] == [1, 2, 3]
    assert all(chunk["char_count"] <= 300 for chunk in chunks)
    assert all("菜名：番茄炒蛋" in chunk["text"] for chunk in chunks)


def test_corpus_reports_duplicate_parent_without_dropping_other_rows(tmp_path: Path):
    first = _parent()
    second = {**first, "document_id": "recipe-2", "name": "另一道菜"}
    input_path = tmp_path / "ready.jsonl"
    input_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in [first, first, second]) + "\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "out"
    summary = build_corpus(input_path, output_dir, max_chars=450, policy_version="test-v1")
    assert summary["parent_count"] == 3
    assert summary["parents_chunked"] == 2
    assert summary["error_count"] == 1
    assert len((output_dir / "chunks.jsonl").read_text(encoding="utf-8").splitlines()) == 2
