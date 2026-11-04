from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from openai import APIConnectionError
from sqlalchemy import select

from app import worker
from app.db.models import WorkspaceMember
from app.db.session import SessionLocal
from app.llm import durable_provider
from app.llm.durable_provider import ConfiguredProvider
from app.services import v2_runtime


async def test_provider_closes_client_and_returns_model_reply(monkeypatch):
    model = SimpleNamespace(
        chat_with_tools=AsyncMock(return_value={"content": "你好", "tool_calls": []}),
        client=SimpleNamespace(close=AsyncMock()),
    )
    monkeypatch.setattr(durable_provider, "build_llm_provider", lambda settings: model)
    response = await ConfiguredProvider().chat_with_tools(messages=[], tools=[])
    assert response["content"] == "你好"
    model.client.close.assert_awaited_once()


async def test_missing_model_is_visible_failure(monkeypatch):
    monkeypatch.setattr(durable_provider, "build_llm_provider", lambda settings: None)
    response = await ConfiguredProvider().chat_with_tools(messages=[], tools=[])
    assert response["error_code"] == "MODEL_NOT_CONFIGURED"


async def test_provider_failure_is_visible_and_client_is_closed(monkeypatch):
    model = SimpleNamespace(
        chat_with_tools=AsyncMock(side_effect=APIConnectionError(
            request=httpx.Request("POST", "https://model.example/chat/completions")
        )),
        client=SimpleNamespace(close=AsyncMock()),
    )
    monkeypatch.setattr(durable_provider, "build_llm_provider", lambda settings: model)
    response = await ConfiguredProvider().chat_with_tools(messages=[], tools=[])
    assert response["error_code"] == "MODEL_REQUEST_FAILED"
    model.client.close.assert_awaited_once()


def test_worker_uses_configured_model_and_preserves_conversation(monkeypatch):
    observed = []

    class Model:
        async def chat_with_tools(self, *, messages, tools):
            observed.append(messages)
            return {"content": "你好，小林", "tool_calls": []}

    model = SimpleNamespace(
        chat_with_tools=Model().chat_with_tools,
        client=SimpleNamespace(close=AsyncMock()),
    )
    # Override only the offline test fixture; exercise the production worker adapter.
    monkeypatch.setattr(durable_provider, "ConfiguredProvider", ConfiguredProvider)
    monkeypatch.setattr(durable_provider, "build_llm_provider", lambda settings: model)
    with SessionLocal() as session:
        member = session.scalar(select(WorkspaceMember))
        actor, household = member.user_id, member.workspace_id
    first = v2_runtime.create_turn(actor, household, None, "我叫小林")
    worker.execute(worker.claim("model-test"), "model-test")
    run = v2_runtime.accessible_run(actor, first["run_id"])
    assert run.status == "succeeded"
    assert run.result["fake"] is False
    second = v2_runtime.create_turn(actor, household, first["session_id"], "我叫什么？")
    worker.execute(worker.claim("model-test"), "model-test")
    assert v2_runtime.accessible_run(actor, second["run_id"]).status == "succeeded"
    assert observed[1][-3:] == [
        {"role": "user", "content": "我叫小林"},
        {"role": "assistant", "content": "你好，小林"},
        {"role": "user", "content": "我叫什么？"},
    ]
