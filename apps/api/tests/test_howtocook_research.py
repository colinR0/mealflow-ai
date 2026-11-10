"""Research-corpus tests. They do not connect to PostgreSQL."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.cli.chunk_recipe_parents import split_parent
from app.services.research_chunks import ChunkTooLong, split_research_parent, verify_step_coverage
from app.services.research_index import (
    require_model_dir,
    require_research_database,
    retrieval_metrics,
    validate_vector,
)
from app.services.research_recipes import (
    build_parent,
    build_research_parents,
    cross_source_reviews,
    deduplicate,
    enforce_load_contract,
)

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations/versions/l7a8b9c0d1e2_recipe_research_staging.py"
)
SOURCE = (
    Path(__file__).resolve().parents[3]
    / "data/sources/pending/howtocook-mock-clean/recipes_howtocook_mock_clean.json"
)


def _entry(**overrides: object) -> dict:
    entry = {
        "name": "陈皮排骨汤",
        "stuff": "排骨、陈皮适量",
        "steps": ["排骨焯水。", "加入陈皮炖汤。"],
        "heat": "中火",
        "difficulty": "简单",
        "category": "汤",
        "tools": "砂锅",
    }
    entry.update(overrides)
    return entry


def _parent() -> dict:
    parent = build_parent(_entry(), source_key="data/sources/x.json", source_sha="a" * 64, index=1)
    parent["document_id"] = "doc-1"
    parent["steps"] = [
        {"ordinal": 1, "text": "番茄切块。"},
        {"ordinal": 2, "text": "鸡蛋炒熟。"},
    ]
    parent["name"] = "番茄炒蛋"
    return parent


def test_duplicate_keeps_one_record_and_does_not_invent_quantities():
    kept, dropped = deduplicate(
        [_entry(), _entry(heat="大火", tools="普通锅"), _entry(name="青椒土豆丝")]
    )
    assert len(kept) == 2
    assert dropped[0]["name"] == "陈皮排骨汤"
    assert dropped[0]["reason"] == "菜名、规范化食材和步骤相同"
    assert dropped[0]["raw_content_differs"] is True
    parent = build_parent(kept[0], source_key="data/sources/x.json", source_sha="b" * 64, index=1)
    assert parent["fields"]["servings"] is None
    assert parent["fields"]["duration_minutes"] is None
    assert parent["fields"]["calories_per_serving"] is None
    assert parent["fields"]["heat"] == "中火"
    assert parent["fields"]["allergens"] is None
    assert parent["publishable"] is False
    assert parent["capabilities"]["publishable_candidate"] is False
    assert parent["capabilities"]["research_candidate"] is True
    assert parent["capabilities"]["allergen_hard_filter"] is False
    assert parent["ingredients"][1]["quantity_status"] == "unknown"
    assert parent["ingredients"][1]["quantity"] is None


def test_same_name_from_another_source_is_kept_separate():
    existing = [{"name": "番茄炒蛋", "document_id": "seed-1"}]
    result = build_research_parents(
        [_entry(name="番茄炒蛋")],
        existing,
        source_key="data/sources/x.json",
        source_sha="c" * 64,
    )
    assert len(result["parents"]) == 2
    assert result["parents"][0]["document_id"] == "seed-1"
    assert cross_source_reviews(existing, result["howtocook"])[0]["action"] == "recorded_only"


def test_contract_rejects_a_short_corpus():
    with pytest.raises(ValueError, match="444"):
        enforce_load_contract(
            {
                "input_count": 2,
                "kept_count": 2,
                "dropped": [],
                "existing_count": 1,
                "parents": [{}, {}],
                "howtocook": [],
            }
        )


def test_official_splitter_still_rejects_unpublished_research_parents():
    with pytest.raises(ValueError, match="切分输入筛选"):
        split_parent(_parent(), max_chars=450, policy_version="recipe-structure-v1")


def test_research_chunk_keeps_every_step_once():
    chunks = split_research_parent(
        _parent(),
        max_chars=450,
        max_tokens=512,
        count_tokens=len,
        policy_version="recipe-structure-research-v1",
    )
    assert chunks[0]["section"] == "full"
    verify_step_coverage(_parent(), chunks)
    assert "番茄适量" not in chunks[0]["text"]
    assert "适量" in chunks[0]["text"]


def test_long_step_splits_on_sentences_without_overlap():
    parent = _parent()
    parent["steps"] = [
        {"ordinal": 1, "text": "先放油。" * 20},
        {"ordinal": 2, "text": "再炒蛋。"},
    ]
    chunks = split_research_parent(
        parent,
        max_chars=48,
        max_tokens=512,
        count_tokens=lambda _text: 1,
        policy_version="test",
    )
    assert [chunk["section"] for chunk in chunks].count("steps") >= 1
    verify_step_coverage(parent, chunks)
    covered = [span["text"] for chunk in chunks for span in chunk["step_spans"]]
    assert covered.count("先放油。") == 20


def test_unsplittable_step_is_a_recorded_failure():
    parent = _parent()
    parent["steps"] = [{"ordinal": 1, "text": "没有句号所以不能拆开的超长步骤" * 8}]
    with pytest.raises(ChunkTooLong):
        split_research_parent(
            parent, max_chars=40, max_tokens=40, count_tokens=len, policy_version="test"
        )


def test_vector_rules_and_database_guard():
    validate_vector([0.1] * 512)
    with pytest.raises(ValueError, match="维度"):
        validate_vector([0.1, 0.2])
    with pytest.raises(ValueError, match="零向量"):
        validate_vector([0.0] * 512)
    require_research_database("postgresql+psycopg://mealflow@127.0.0.1:5434/mealflow_s7")
    with pytest.raises(RuntimeError, match="mealflow_rag"):
        require_research_database("postgresql+psycopg://mealflow@127.0.0.1:5434/mealflow_rag")


def test_metrics_count_exclusion_without_treating_similarity_as_safety():
    questions = [
        {
            "question_id": "q1",
            "answerable": True,
            "acceptable_document_ids": ["a"],
            "must_exclude_document_ids": ["b"],
        },
        {
            "question_id": "q2",
            "answerable": False,
            "acceptable_document_ids": [],
            "must_exclude_document_ids": [],
        },
    ]
    metrics = retrieval_metrics(questions, {"q1": ["b", "a"], "q2": ["c"]})
    assert metrics["hit_at_5"] == 1.0
    assert metrics["exclusion_violations"][0]["document_ids"] == ["b"]
    assert metrics["false_recall_examples"][0]["question_id"] == "q2"
    assert metrics["allergen_hard_filter"] is False


def test_migration_does_not_touch_the_business_vector_column():
    text = MIGRATION.read_text(encoding="utf-8")
    assert 'down_revision = "h3c4d5e6f7a8"' in text
    assert "CREATE SCHEMA IF NOT EXISTS recipe_research" in text
    assert "vector(512)" in text
    assert "knowledge_chunks" not in text
    assert "DROP COLUMN" not in text
    assert "production_publishable = false" in text


def test_missing_model_stops():
    with pytest.raises(FileNotFoundError, match="嵌入模型不存在"):
        require_model_dir(Path("D:/missing-bge-model"))


@pytest.mark.skipif(not SOURCE.is_file(), reason="本地清洗 JSON 未放置")
def test_real_source_matches_the_handoff_contract():
    from app.services.research_recipes import load_entries, load_jsonl

    existing_path = SOURCE.parents[4] / "output/prechunk-audit-v1/ready_for_chunking.jsonl"
    result = build_research_parents(
        load_entries(SOURCE),
        load_jsonl(existing_path),
        source_key="data/sources/pending/howtocook-mock-clean/recipes_howtocook_mock_clean.json",
        source_sha="b" * 64,
    )
    enforce_load_contract(result)
    assert result["dropped"][0]["raw_content_differs"] is True
