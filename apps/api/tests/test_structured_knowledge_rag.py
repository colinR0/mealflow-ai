"""Three isolated structured libraries: recall IDs, then read typed fields."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import select
from test_native_postgres import native_pg  # noqa: F401
from test_recipe_rag_flow import rag_turn  # noqa: F401

from app.agent import durable_graph as graph
from app.agent.tool_runtime import QueryInput
from app.core.config import Settings
from app.db.v2_models import FlavorTemplate, FoodRiskRule
from app.services.structured_knowledge import search_structured

SEED = Path(__file__).resolve().parents[3] / "scripts" / "structured_food_knowledge.seed.json"


@pytest.fixture
def structured_library(rag_turn, monkeypatch):  # noqa: F811
    sessions, actor = rag_turn
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    import import_structured_food_knowledge as importer

    monkeypatch.setattr(importer, "SessionLocal", sessions)
    monkeypatch.setattr(importer, "prepare_import", lambda: None)
    monkeypatch.setattr(importer, "PROFILE", "profile")
    plan = importer.build_plan(SEED)
    result = importer.apply_plan(plan)
    assert result == {"created": 16, "updated": 0}
    return sessions, actor, importer, plan


def test_substitution_reads_factor_and_structured_fields(structured_library):
    sessions, _actor, _importer, _plan = structured_library
    result = search_structured(
        "substitution",
        "生抽",
        sessions=sessions,
        settings=Settings(_env_file=None, retrieval_mode="native"),
    )
    assert result.status == "ok"
    assert result.data["search_stage"] == "ids_then_structured_rows"
    assert result.data["fake"] is True
    assert result.evidence_ids[0].startswith("substitution:")
    item = result.data["evidence"][0]
    assert item["kind"] == "substitution"
    assert item["content"]["from_ingredient_name"] == "生抽"
    assert item["content"]["to_ingredient_name"] == "鲜酱油"
    assert item["content"]["conversion_factor"] == "1.0000"
    assert item["content"]["unit"] == "份"


def test_fake_recall_requires_keyword_hit(structured_library):
    sessions, _actor, _importer, _plan = structured_library
    result = search_structured(
        "substitution",
        "完全不存在的食材",
        sessions=sessions,
        settings=Settings(_env_file=None, retrieval_mode="native"),
    )
    assert result.status == "no_result"
    assert "fake 检索只接受关键词命中候选" in result.warnings


def test_demo_mode_uses_the_same_structured_rows(structured_library):
    sessions, _actor, _importer, _plan = structured_library
    result = search_structured(
        "substitution",
        "生抽",
        sessions=sessions,
        settings=Settings(_env_file=None, retrieval_mode="demo"),
    )
    assert result.status == "ok"
    assert result.data["mode"] == "demo"
    assert result.data["evidence"][0]["content"]["to_ingredient_name"] == "鲜酱油"


def test_draft_rows_are_not_retrievable(structured_library):
    sessions, _actor, _importer, _plan = structured_library
    flavor = search_structured(
        "flavor", "酸甜口", sessions=sessions,
        settings=Settings(_env_file=None, retrieval_mode="native"),
    )
    risk = search_structured(
        "risk", "孕期", sessions=sessions,
        settings=Settings(_env_file=None, retrieval_mode="native"),
    )
    assert flavor.status == "no_result"
    assert flavor.data["empty_library"] is True
    assert risk.status == "no_result"
    assert risk.data["empty_library"] is True


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
    settings = Settings(_env_file=None, retrieval_mode="native")
    flavor = search_structured("flavor", "酸甜口", sessions=sessions, settings=settings)
    assert flavor.status == "ok"
    assert flavor.evidence_ids[0].startswith("flavor:")
    content = flavor.data["evidence"][0]["content"]
    assert content["components"][0]["parts"] == 1.0
    assert content["ratio_basis"] == "volume"

    risk_result = search_structured("risk", "婴幼儿", sessions=sessions, settings=settings)
    assert risk_result.status == "ok"
    assert risk_result.evidence_ids[0].startswith("risk:")
    risk_content = risk_result.data["evidence"][0]["content"]
    assert risk_content["applies_to"] == ["infant"]
    assert risk_content["min_age_months"] == 0
    assert risk_content["evidence_level"] == "limited"


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
            results = [json.loads(message["content"]) for message in messages
                       if message["role"] == "tool"]
            if not results:
                return {"tool_calls": [{"id": "sub", "type": "function", "function": {
                    "name": "find_ingredient_substitute",
                    "arguments": json.dumps({"query": "生抽"}),
                }}]}
            evidence = results[-1]["evidence_ids"][0]
            return {"content": "可以用鲜酱油等量替换。" + (
                f" [{evidence}]" if self.cite else ""
            )}

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
    source.write_text(json.dumps({"food_risks": [{
        "key": "bad",
        "review_status": "approved",
        "source_name": "test",
        "risk_type": "infant",
        "title": "bad",
        "applies_to": ["infant"],
        "evidence_level": "unverified",
    }]}), encoding="utf-8")
    with pytest.raises(ValueError, match="unverified"):
        importer.build_plan(source)