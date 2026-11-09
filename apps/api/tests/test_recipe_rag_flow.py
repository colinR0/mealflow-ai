"""Exercise recipe retrieval through the durable graph and persisted turn result."""

import json

import pytest
from sqlalchemy import select
from test_durable_graph import start
from test_native_postgres import native_pg  # noqa: F401

from app import worker
from app.agent import durable_graph as graph
from app.core.config import get_settings
from app.db.models import User, Workspace, WorkspaceMember
from app.db.v2_models import KnowledgeDocument, KnowledgeSource, TurnRun
from app.services import v2_runtime as runtime
from app.services.native_index_worker import rebuild_generation


class RecipeProvider:
    def __init__(self, *, cite=True):
        self.cite = cite

    async def chat_with_tools(self, *, messages, tools):
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        if not results:
            return {"tool_calls": [{"id": "search", "type": "function", "function": {
                "name": "search_recipes", "arguments": json.dumps({"query": "番茄"}),
            }}]}
        evidence = results[-1]["data"].get("evidence", [])
        if not evidence:
            return {"content": "模型不应在查询失败后补写的做法。"}
        item = evidence[0]
        citation = f" [{item['id']}]" if self.cite else ""
        return {"content": "\n".join(item["content"]["steps"]) + citation}


@pytest.fixture
def rag_turn(native_pg, monkeypatch):  # noqa: F811
    from sqlalchemy.orm import sessionmaker

    sessions = sessionmaker(bind=native_pg, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(runtime, "SessionLocal", sessions)
    monkeypatch.setattr(get_settings(), "retrieval_mode", "native")
    with sessions.begin() as session:
        user = User(display_name="RAG", password_hash="unused")
        household = Workspace(name="RAG test", kind="personal")
        session.add_all([user, household])
        session.flush()
        session.add(WorkspaceMember(workspace_id=household.id, user_id=user.id,
                                    role="user", status="active"))
        for doc in session.scalars(select(KnowledgeDocument)):
            doc.content = {"name": "番茄菜谱", "steps": ["洗净番茄。", "煮熟后盛出。"],
                           "ingredients": [{"name": "番茄", "quantity": None, "unit": None}]}
        actor = user.id, household.id
    with native_pg.connect() as connection:
        rebuild_generation(connection, profile_id="profile")
    return sessions, actor


def test_native_rag_turn_persists_steps_and_inline_citation(rag_turn):
    sessions, actor = rag_turn
    _, response, snapshot = start(sessions, actor=actor, message="番茄怎么做")
    result = graph.compute(sessions, snapshot, RecipeProvider())
    assert result["status"] == "succeeded"
    assert "洗净番茄。\n煮熟后盛出。" in result["text"]
    assert result["citations"] == [result["evidence"][0]["id"]]
    assert any("fake embedding" in warning for warning in result["warnings"])
    worker.finish(snapshot, result)
    with sessions() as session:
        saved = session.get(TurnRun, response["run_id"])
        assert saved.status == "succeeded"
        assert saved.result["citations"] == result["citations"]
        assert saved.result["evidence"][0]["content"]["steps"] == ["洗净番茄。", "煮熟后盛出。"]


def test_recipe_answer_requires_citation(rag_turn):
    sessions, actor = rag_turn
    _, _, snapshot = start(sessions, actor=actor, message="番茄怎么做")
    result = graph.compute(sessions, snapshot, RecipeProvider(cite=False))
    assert result["status"] == "failed"
    assert result["error_code"] == "MISSING_RECIPE_CITATION"
    assert result["evidence"] == []


def test_revoked_source_returns_no_result_without_invented_steps(rag_turn):
    sessions, actor = rag_turn
    with sessions.begin() as session:
        session.get(KnowledgeSource, "source").approval = "revoked"
    _, _, snapshot = start(sessions, actor=actor, message="番茄怎么做")
    result = graph.compute(sessions, snapshot, RecipeProvider())
    assert result["status"] == "waiting_input"
    assert "未查到" in result["text"]
    assert "补写" not in result["text"]
    assert result["citations"] == []


def test_missing_index_is_failure_instead_of_no_result(rag_turn):
    from app.db.v2_models import IndexGeneration

    sessions, actor = rag_turn
    with sessions.begin() as session:
        for generation in session.scalars(select(IndexGeneration)):
            generation.active = False
    _, _, snapshot = start(sessions, actor=actor, message="番茄怎么做")
    result = graph.compute(sessions, snapshot, RecipeProvider())
    assert result["status"] == "failed"
    assert result["error_code"] == "NO_ACTIVE_INDEX"
    assert "暂时不可用" in result["text"]
