"""Typed SQL lookup for the three isolated structured libraries."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, update
from test_native_postgres import native_pg  # noqa: F401
from test_recipe_rag_flow import rag_turn  # noqa: F401

from app.agent import durable_graph as graph
from app.agent.tool_runtime import QueryInput
from app.db.v2_models import FlavorTemplate, FoodRiskRule
from app.services.structured_knowledge import search_structured

SEED = Path(__file__).resolve().parents[3] / "scripts" / "structured_food_knowledge.seed.json"


@pytest.fixture
def structured_library(rag_turn, monkeypatch):  # noqa: F811
    sessions, actor = rag_turn
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    import import_structured_food_knowledge as importer

    monkeypatch.setattr(importer, "SessionLocal", sessions)
    plan = importer.build_plan(SEED)
    assert importer.apply_plan(plan) == {"created": len(plan), "updated": 0}
    return sessions, actor, importer, plan


def test_substitution_reads_factor_and_structured_fields(structured_library):
    sessions, _actor, _importer, _plan = structured_library
    result = search_structured("substitution", "生抽", sessions=sessions)
    assert result.status == "ok"
    assert result.data["search_stage"] == "typed_lookup"
    assert result.data["empty_library"] is False
    assert result.evidence_ids[0].startswith("substitution:")
    item = next(
        entry
        for entry in result.data["evidence"]
        if entry["content"]["to_ingredient_name"] == "鲜酱油"
    )
    assert item["kind"] == "substitution"
    assert item["content"]["from_ingredient_name"] == "生抽"
    assert item["content"]["conversion_factor"] == "1.0000"
    assert item["content"]["unit"] == "份"


def test_unknown_input_returns_no_result_without_a_safe_claim(structured_library):
    sessions, _actor, _importer, _plan = structured_library
    result = search_structured("substitution", "完全不存在的食材", sessions=sessions)
    assert result.status == "no_result"
    assert result.evidence_ids == []
    assert "这不代表饮食安全" in result.warnings[0]
    assert "safe" not in json.dumps(result.data, ensure_ascii=False).lower()


def test_draft_rows_are_never_retrieved(structured_library):
    sessions, _actor, _importer, _plan = structured_library
    with sessions.begin() as session:
        draft_ids = set(
            session.scalars(
                select(FlavorTemplate.id).where(FlavorTemplate.review_status == "draft")
            )
        )
        draft_name = session.scalar(
            select(FlavorTemplate.name)
            .where(FlavorTemplate.review_status == "draft")
            .order_by(FlavorTemplate.id)
            .limit(1)
        )
    assert draft_ids and draft_name
    result = search_structured("flavor", draft_name, sessions=sessions)
    assert result.status in {"ok", "no_result"}
    retrieved = {item["id"].split(":")[1] for item in result.data["evidence"]}
    assert not retrieved & draft_ids


def test_empty_library_is_reported_when_no_approved_rows_remain(structured_library):
    sessions, _actor, _importer, _plan = structured_library
    with sessions.begin() as session:
        session.execute(update(FlavorTemplate).values(review_status="draft"))
    result = search_structured("flavor", "酸甜口", sessions=sessions)
    assert result.status == "no_result"
    assert result.data["empty_library"] is True
    assert "该库暂无已审核的结构化数据" in result.warnings


def test_approved_flavor_and_risk_read_their_own_fields(structured_library):
    sessions, _actor, _importer, _plan = structured_library
    with sessions.begin() as session:
        flavor = session.scalar(
            select(FlavorTemplate).where(FlavorTemplate.name == "酸甜口")
        )
        flavor.review_status = "approved"
        risk = session.scalar(select(FoodRiskRule).where(FoodRiskRule.risk_type == "infant"))
        risk.review_status = "approved"
        risk.evidence_level = "limited"
    flavor_result = search_structured("flavor", "酸甜口", sessions=sessions)
    assert flavor_result.status == "ok"
    assert flavor_result.evidence_ids[0].startswith("flavor:")
    content = flavor_result.data["evidence"][0]["content"]
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    expected = next(item for item in seed["flavor_templates"] if item["name"] == "酸甜口")
    assert content["components"] == expected["components"]
    assert content["ratio_basis"] == expected["ratio_basis"]

    risk_result = search_structured("risk", "婴幼儿辅食", sessions=sessions)
    assert risk_result.status == "ok"
    assert risk_result.evidence_ids[0].startswith("risk:")
    risk_content = risk_result.data["evidence"][0]["content"]
    assert risk_content["applies_to"] == ["infant"]
    assert risk_content["min_age_months"] == 0
    assert risk_content["evidence_level"] == "limited"


def test_flavor_alias_is_a_typed_match(structured_library):
    sessions, _actor, _importer, _plan = structured_library
    with sessions.begin() as session:
        flavor = session.scalar(select(FlavorTemplate).where(FlavorTemplate.name == "鱼香"))
        flavor.review_status = "approved"
    result = search_structured("flavor", "鱼香口", sessions=sessions)
    assert result.status == "ok"
    assert result.data["evidence"][0]["title"] == "鱼香"


def test_structured_tables_have_no_vector_columns(structured_library):
    sessions, _actor, _importer, _plan = structured_library
    with sessions() as session:
        inspector = inspect(session.get_bind())
        columns = {
            table: {column["name"] for column in inspector.get_columns(table)}
            for table in ("ingredient_substitutions", "flavor_templates", "food_risk_rules")
        }
    removed = {"rag_text", "embedding", "embedding_profile_id", "embedding_vector", "search_tsv"}
    for table, names in columns.items():
        assert not names & removed, table


def test_substitution_tool_and_citation_validation(structured_library):
    sessions, actor, _importer, _plan = structured_library
    runtime = graph.Runtime(sessions, None)
    tool_result = asyncio.run(runtime.find_ingredient_substitute(QueryInput(query="生抽")))
    assert tool_result.status == "ok"

    from test_durable_graph import start

    class Provider:
        def __init__(self, cite):
            self.cite = cite

        async def chat_with_tools(self, *, messages, tools):
            results = [
                json.loads(message["content"])
                for message in messages
                if message["role"] == "tool"
            ]
            if not results:
                return {
                    "tool_calls": [
                        {
                            "id": "sub",
                            "type": "function",
                            "function": {
                                "name": "find_ingredient_substitute",
                                "arguments": json.dumps({"query": "生抽"}),
                            },
                        }
                    ]
                }
            evidence = results[-1]["evidence_ids"][0]
            return {
                "content": "可以用鲜酱油等量替换。"
                + (f" [{evidence}]" if self.cite else "")
            }

    _, _, snapshot = start(sessions, actor=actor, message="生抽怎么换")
    cited = graph.compute(sessions, snapshot, Provider(cite=True))
    assert cited["status"] == "succeeded", cited
    assert cited["citations"][0].startswith("substitution:")

    _, _, snapshot = start(sessions, actor=actor, message="生抽怎么换")
    missing = graph.compute(sessions, snapshot, Provider(cite=False))
    assert missing["status"] == "failed"
    assert missing["error_code"] == "MISSING_STRUCTURED_CITATION"


def test_import_is_idempotent(structured_library):
    _sessions, _actor, importer, plan = structured_library
    assert importer.apply_plan(plan) == {"created": 0, "updated": 0}


def test_approved_risk_cannot_have_unverified_evidence(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    import import_structured_food_knowledge as importer

    source = tmp_path / "bad.json"
    source.write_text(
        json.dumps(
            {
                "food_risks": [
                    {
                        "key": "bad",
                        "review_status": "approved",
                        "source_name": "test",
                        "risk_type": "infant",
                        "title": "bad",
                        "applies_to": ["infant"],
                        "evidence_level": "unverified",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unverified"):
        importer.build_plan(source)