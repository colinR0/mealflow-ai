"""Embedding pilot tests use injected vectors and do not download a model."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.recipe_embeddings import (
    DIMENSIONS,
    INSTRUCTION_VERSION,
    MODEL_NAME,
    embedding_profile,
    pilot_metrics,
    query_text,
    rank_by_cosine,
    require_model_dir,
)


def _chunk(document_id: str, section: str, text: str) -> dict:
    return {
        "chunk_id": f"{document_id}-{section}",
        "document_id": document_id,
        "name": "蒸鱼",
        "section": section,
        "content_version": "v1",
        "text": text,
    }


def test_profile_records_model_dimension_and_instruction():
    profile = embedding_profile()
    assert profile["model_name"] == MODEL_NAME
    assert profile["dimensions"] == DIMENSIONS == 1024
    assert profile["instruction_version"] == INSTRUCTION_VERSION
    assert profile["query_task"] in query_text("番茄炒蛋怎么做")
    assert profile["document_instruction"] == ""


def test_same_parent_keeps_the_closer_chunk_only():
    overview = _chunk("fish", "overview", "概览")
    steps = _chunk("fish", "steps", "步骤")
    vectors = {overview["chunk_id"]: [1.0, 0.0], steps["chunk_id"]: [0.0, 1.0]}
    ranked = rank_by_cosine([0.0, 1.0], [overview, steps], vectors)
    assert [item["chunk_id"] for item in ranked] == [steps["chunk_id"]]
    assert ranked[0]["section"] == "steps"


def test_zero_vector_is_not_a_hit():
    chunk = _chunk("egg", "full", "番茄炒蛋")
    vectors = {chunk["chunk_id"]: [0.0, 0.0]}
    assert rank_by_cosine([1.0, 0.0], [chunk], vectors) == []


def test_dimension_mismatch_raises():
    chunk = _chunk("egg", "full", "番茄炒蛋")
    with pytest.raises(ValueError, match="维度"):
        rank_by_cosine([1.0, 0.0, 0.0], [chunk], {chunk["chunk_id"]: [1.0, 0.0]})


def test_metrics_match_the_string_baseline_definitions():
    questions = [
        {
            "question_id": "q1",
            "answerable": True,
            "acceptable_document_ids": ["a", "b"],
        }
    ]
    metrics = pilot_metrics(questions, {"q1": ["a", "c"]})
    assert metrics["hit_at_5"] == 1
    assert metrics["recall_at_5"] == 0.5
    assert metrics["precision_at_5"] == 0.5
    assert metrics["mrr_at_10"] == 1


def test_missing_model_directory_stops(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="嵌入模型不存在"):
        require_model_dir(tmp_path / "Qwen3-Embedding-0.6B")
