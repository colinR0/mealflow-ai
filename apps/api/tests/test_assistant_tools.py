"""Functional tool and retirement checks. Uses the explicitly configured disposable DB."""

import asyncio
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes.v2 import identity
from app.db.models import Ingredient, Workspace, WorkspaceMember
from app.db.session import SessionLocal
from app.db.v2_models import (
    BackgroundJob,
    HouseholdProfile,
    InventoryLot,
    PendingAction,
    ShoppingItem,
    TurnRun,
)
from app.main import app
from app.services import v2_runtime as runtime
from app.services.research_index import (
    DIMENSIONS,
    INSTRUCTION_VERSION,
    MODEL_NAME,
    QUERY_PREFIX,
)
from app.services.research_retrieval import RecipeRetriever, RetrievalError
from app.tools.assistant import AssistantTools, ToolContext
from app.tools.contracts import Tool, ToolInput, ToolResult, dispatch
from app.worker import claim, execute


@pytest.fixture
def tools():
    with SessionLocal() as session:
        member = session.scalar(select(WorkspaceMember).order_by(WorkspaceMember.id))
        actor, household = member.user_id, member.workspace_id
    return AssistantTools(
        ToolContext(
            actor_id=actor,
            household_id=household,
            sessions=SessionLocal,
            batch_id="fixed-batch",
            model_dir=Path("missing-model"),
        )
    )


def call(tools, tool_name, **arguments):
    return asyncio.run(tools.call(tool_name, json.dumps(arguments, ensure_ascii=False)))


def test_six_declarations_and_injected_context(tools):
    declarations = tools.declarations()
    assert {row["function"]["name"] for row in declarations} == {
        "search_recipes",
        "get_recipe_detail",
        "get_inventory_summary",
        "get_member_profiles",
        "get_procurement_list",
        "prepare_procurement_action",
    }
    for row in declarations:
        schema = row["function"]["parameters"]
        assert schema["additionalProperties"] is False
        assert (
            not {"actor_id", "household_id", "batch_id", "model_dir"} & schema["properties"].keys()
        )
    assert call(tools, "not_a_tool").error_code == "UNKNOWN_TOOL"
    assert (
        call(tools, "search_recipes", query="x", actor_id="someone").error_code
        == "INVALID_ARGUMENTS"
    )
    assert call(tools, "search_recipes", query="  ").error_code == "INVALID_ARGUMENTS"


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("search_recipes", {"query": "鸡蛋"}),
        ("get_recipe_detail", {"document_id": "x", "content_version": "a" * 64}),
        ("get_inventory_summary", {}),
        ("get_member_profiles", {}),
        ("get_procurement_list", {}),
        ("prepare_procurement_action", {"name": "紫苏"}),
    ],
)
def test_every_tool_denies_foreign_household(tools, name, arguments):
    with SessionLocal.begin() as session:
        other = Workspace(name="Foreign", kind="personal")
        session.add(other)
        session.flush()
        other_id = other.id
    foreign = AssistantTools(
        ToolContext(
            actor_id=tools.context.actor_id,
            household_id=other_id,
            sessions=SessionLocal,
            batch_id="fixed",
        )
    )
    assert call(foreign, name, **arguments).status == "error"
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(PendingAction)) == 0


def test_hybrid_adapter_caches_retriever_and_preserves_evidence(tools, monkeypatch):
    calls = []

    def search(self, query, top_k, mode):
        calls.append((id(self), query, top_k, mode))
        return {
            "hits": [
                {"document_id": "doc", "content_version": "a" * 64, "snippet": {"text": "原文"}}
            ]
        }

    monkeypatch.setattr(RecipeRetriever, "search", search)
    first = call(tools, "search_recipes", query="鸡蛋", top_k=3)
    second = call(tools, "search_recipes", query="番茄")
    assert first.status == second.status == "ok"
    assert [row[3] for row in calls] == ["hybrid", "hybrid"]
    assert calls[0][0] == calls[1][0]
    assert first.evidence_ids == ["research:fixed-batch:doc:" + "a" * 64]
    assert first.data["hits"][0]["snippet"]["text"] == "原文"


def test_missing_recipe_config_does_not_disable_business_tools(tools):
    no_index = AssistantTools(
        ToolContext(
            actor_id=tools.context.actor_id,
            household_id=tools.context.household_id,
            sessions=SessionLocal,
        )
    )
    assert call(no_index, "search_recipes", query="鸡蛋").error_code == "index_config"
    assert call(no_index, "get_inventory_summary").status in {"ok", "no_result"}


def test_inventory_active_pagination_alias_and_unknown(tools):
    with SessionLocal.begin() as session:
        for key, status, quantity in [
            ("tomato", "active", None),
            ("egg", "active", Decimal("3")),
            ("potato", "removed", None),
        ]:
            ingredient = session.scalar(select(Ingredient).where(Ingredient.key == key))
            session.add(
                InventoryLot(
                    workspace_id=tools.context.household_id,
                    ingredient_id=ingredient.id,
                    ingredient_key=key,
                    name=ingredient.name,
                    quantity_status="unknown" if quantity is None else "known",
                    quantity=quantity,
                    unit=None if quantity is None else "个",
                    confirmed_at=runtime.utc_now(),
                    source_type="test",
                    source_id=key,
                    status=status,
                    version=1,
                )
            )
    result = call(tools, "get_inventory_summary", limit=1)
    assert result.status == "ok" and result.data["total"] == 2
    assert len(result.data["items"]) == 1
    second = call(tools, "get_inventory_summary", offset=1, limit=1)
    assert result.data["items"][0]["id"] != second.data["items"][0]["id"]
    tomato = call(tools, "get_inventory_summary", search="西红柿")
    assert tomato.data["total"] == 1
    assert tomato.data["items"][0]["quantity"] is None
    assert tomato.data["items"][0]["quantity_status"] == "unknown"
    assert call(tools, "get_inventory_summary", offset=100).data["total"] == 2


def test_profiles_dates_raw_restrictions_and_selection(tools):
    day = date(2026, 11, 14)
    events = [
        {"id": "today", "start_date": day.isoformat(), "end_date": day.isoformat()},
        {"id": "past", "start_date": "2026-10-01", "end_date": "2026-10-03"},
        {"id": "future", "start_date": "2026-12-01", "end_date": "2026-12-03"},
        {"id": "bad", "start_date": "unknown", "end_date": "2026-12-03"},
        {"id": "reverse", "start_date": "2026-12-03", "end_date": "2026-12-01"},
    ]
    with SessionLocal.begin() as session:
        profile = HouseholdProfile(
            workspace_id=tools.context.household_id,
            display_name="小王",
            restrictions=["原文忌口", {"type": "allergy", "ingredient_key": "peanut"}],
            preferences=["清淡"],
            legacy_details={"events": events},
            version=2,
        )
        session.add(profile)
        session.flush()
        profile_id = profile.id
    result = call(tools, "get_member_profiles", member_ids=[profile_id], as_of=day.isoformat())
    assert result.status == "partial"
    item = result.data["items"][0]
    assert [row["id"] for row in item["active_events"]] == ["today"]
    assert item["restrictions"][0] == "原文忌口"
    assert item["allergy_information"] == "recorded"
    assert result.data_versions[profile_id] == 2
    assert (
        call(tools, "get_member_profiles", member_ids=["missing"]).error_code == "PROFILE_NOT_FOUND"
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {"name": "紫苏", "quantity_status": "known"},
        {"name": "紫苏", "quantity": "1"},
        {"name": "紫苏", "quantity_status": "known", "quantity": "-1", "unit": "kg"},
        {"name": "紫苏", "quantity_status": "known", "quantity": "0.0001", "unit": "kg"},
    ],
)
def test_procurement_quantity_validation(tools, arguments):
    assert call(tools, "prepare_procurement_action", **arguments).error_code == "INVALID_ARGUMENTS"


def test_prepare_arbitrary_food_then_confirm_once_and_list(tools):
    with SessionLocal() as session:
        ingredient_count = session.scalar(select(func.count()).select_from(Ingredient))
        shopping_count = session.scalar(select(func.count()).select_from(ShoppingItem))
    prepared = call(tools, "prepare_procurement_action", name="新鲜紫苏叶", note="今晚用")
    assert prepared.status == "ok"
    action = prepared.data["pending_action"]
    assert prepared.data["requires_confirmation"] is True
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ShoppingItem)) == shopping_count
        assert session.scalar(select(func.count()).select_from(Ingredient)) == ingredient_count
        assert session.get(PendingAction, action["id"]).run_id is None
    app.dependency_overrides[identity] = lambda: (tools.context.actor_id, "test-session")
    try:
        with TestClient(app) as client:
            kwargs = {
                "headers": {"Idempotency-Key": "confirm-purple"},
                "json": {
                    "expected_version": action["version"],
                    "payload_hash": action["payload_hash"],
                },
            }
            first = client.post(f"/api/v2/actions/{action['id']}/confirm", **kwargs)
            assert first.status_code == 200
            assert (
                client.post(f"/api/v2/actions/{action['id']}/confirm", **kwargs).json()
                == first.json()
            )
    finally:
        app.dependency_overrides.pop(identity, None)
    listing = call(tools, "get_procurement_list")
    assert listing.data["total"] == shopping_count + 1
    row = next(row for row in listing.data["items"] if row["name"] == "新鲜紫苏叶")
    assert row["quantity"] is None and row["unit"] is None and row["ingredient_key"] == "unknown"
    with SessionLocal.begin() as session:
        session.get(ShoppingItem, row["id"]).status = "purchased"
    assert call(tools, "get_procurement_list").data["total"] == shopping_count
    assert call(tools, "get_procurement_list", status=None).data["total"] == shopping_count + 1


def test_known_procurement_alias_version_expiry_and_rejection(tools):
    result = call(
        tools,
        "prepare_procurement_action",
        name="西红柿",
        quantity_status="known",
        quantity="1.25",
        unit="kg",
    )
    action = result.data["pending_action"]
    assert action["payload"]["ingredient_key"] == "tomato"
    with pytest.raises(HTTPException) as stale:
        runtime.decide_action(
            tools.context.actor_id, action["id"], True, "stale", 99, action["payload_hash"]
        )
    assert stale.value.status_code == 409
    with SessionLocal.begin() as session:
        session.get(PendingAction, action["id"]).expires_at = runtime.utc_now() - timedelta(
            seconds=1
        )
    with pytest.raises(HTTPException) as expired:
        runtime.decide_action(tools.context.actor_id, action["id"], True, "expired")
    assert expired.value.status_code == 409
    second = call(tools, "prepare_procurement_action", name="紫苏").data["pending_action"]
    rejected = runtime.decide_action(tools.context.actor_id, second["id"], False, "reject")
    assert rejected["status"] == "rejected"


def test_retired_api_does_not_enqueue_and_worker_drains_legacy_jobs(tools):
    app.dependency_overrides[identity] = lambda: (tools.context.actor_id, "test-session")
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v2/agent/turn-runs",
                headers={"Idempotency-Key": "new-turn"},
                json={"household_id": tools.context.household_id, "text": "你好"},
            )
            assert response.status_code == 503
            assert response.json()["detail"]["code"] == "AGENT_REBUILDING"
            for path in ("/api/v1/agent/runs", "/api/v1/chat/stream", "/api/v1/meal-plans/confirm"):
                assert client.post(path, json={}).status_code == 404
    finally:
        app.dependency_overrides.pop(identity, None)
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(BackgroundJob)) == 0
    legacy = runtime.enqueue_turn(
        tools.context.actor_id, tools.context.household_id, None, "旧任务"
    )
    with SessionLocal.begin() as session:
        session.get(TurnRun, legacy["run_id"]).created_at = runtime.utc_now() - timedelta(days=1)
    execute(claim("retirement-worker"), "retirement-worker")
    run = runtime.accessible_run(tools.context.actor_id, legacy["run_id"])
    assert run.status == "failed" and run.error_code == "AGENT_REBUILDING"
    with SessionLocal() as session:
        assert session.scalar(select(BackgroundJob)).status == "done"
        assert session.scalar(select(func.count()).select_from(PendingAction)) == 0


def test_model_reused_and_fts_skips_encoding(monkeypatch):
    from app.services import research_retrieval

    created = []

    class Encoder:
        def __init__(self, path):
            created.append(path)
            self.profile = {
                "model_name": MODEL_NAME,
                "dimensions": DIMENSIONS,
                "query_prefix": QUERY_PREFIX,
                "instruction_version": INSTRUCTION_VERSION,
                "normalize": True,
                "max_position_embeddings": 512,
            }

        def count_tokens(self, text):
            return 10

        def embed_queries(self, queries):
            return [[1.0] + [0.0] * 511]

    monkeypatch.setattr(research_retrieval, "BgeEmbedder", Encoder)
    retriever = RecipeRetriever(
        engine=SessionLocal.kw["bind"], model_dir=Path("unused"), batch_id="x"
    )
    retriever._embed("鸡蛋", {})
    retriever._embed("番茄", {})
    assert len(created) == 1


def test_errors_and_async_timeout_are_structured(tools, monkeypatch):
    def failed(*args):
        raise SQLAlchemyError("private driver detail")

    monkeypatch.setattr(tools, "_session", failed)
    result = call(tools, "get_inventory_summary")
    assert result.status == "unavailable" and result.error_code == "DATABASE_UNAVAILABLE"
    assert "private" not in result.model_dump_json()

    async def slow(body):
        await asyncio.sleep(1)
        return ToolResult(status="ok")

    tool = Tool("slow", "slow", ToolInput, "read", slow, timeout=0.001)
    assert asyncio.run(dispatch(tool, "{}")).error_code == "TOOL_TIMEOUT"


def test_cli_reads_utf8_arguments_without_agent(tools, tmp_path, capsys, monkeypatch):
    from app.cli.call_assistant_tool import main
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "research_retrieval_batch_id", "")
    path = tmp_path / "arguments.json"
    path.write_text('{"search":"不存在的食材"}', encoding="utf-8")
    args = [
        "--actor-id",
        tools.context.actor_id,
        "--household-id",
        tools.context.household_id,
        "--tool",
        "get_inventory_summary",
        "--arguments-file",
        str(path),
    ]
    assert main(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "no_result" and output["data"]["total"] == 0
    path.write_text('{"actor_id":"foreign"}', encoding="utf-8")
    assert main(args) == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == "INVALID_ARGUMENTS"


def test_detail_adapter_does_not_load_model(tools, monkeypatch):
    def detail(self, document_id, content_version):
        assert self._embedder is None
        return {"document": {"payload": {"name": "番茄炒蛋"}}, "status": "ok"}

    monkeypatch.setattr(RecipeRetriever, "get_detail", detail)
    result = call(tools, "get_recipe_detail", document_id="doc", content_version="a" * 64)
    assert result.status == "ok"
    assert result.data["document"]["payload"]["name"] == "番茄炒蛋"

    def mismatch(*args):
        raise RetrievalError("evidence_mismatch", "changed")

    monkeypatch.setattr(RecipeRetriever, "get_detail", mismatch)
    assert (
        call(tools, "get_recipe_detail", document_id="doc", content_version="a" * 64).status
        == "error"
    )
