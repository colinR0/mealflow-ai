"""Skill isolation, fallback failures and citations through the durable runtime."""

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest
from test_native_postgres import native_pg  # noqa: F401
from test_recipe_rag_flow import rag_turn  # noqa: F401

from app.agent import durable_graph as graph
from app.agent.tool_runtime import QueryInput, ToolResult
from app.core.config import Settings
from app.db.v2_models import KnowledgeDocument
from app.services import retrieval_gateway as gateway
from app.services import web_knowledge_search as web
from app.services.native_index_worker import rebuild_generation


def test_markdown_import_preserves_body_and_is_idempotent(rag_turn, monkeypatch, tmp_path):  # noqa: F811
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    import import_cooking_skills as importer

    sessions, _actor = rag_turn
    monkeypatch.setattr(importer, "SessionLocal", sessions)
    monkeypatch.setattr(importer, "prepare_import", lambda: None)
    monkeypatch.setattr(importer, "ensure_generation", lambda: "generation")
    path = tmp_path / "skill.md"
    raw = "# 焯水\n\n## 操作\n冷水下锅。\n"
    path.write_text(raw, encoding="utf-8")
    plan = importer.build_plan(tmp_path)
    assert plan[0]["content"]["markdown"] == raw
    assert "焯水 > 操作" in plan[0]["content"]["sections"]
    assert importer.apply_plan(plan)["created"] == 1
    assert importer.apply_plan(plan) == {
        "created": 0, "updated": 0, "generation_id": "generation"}
    path.write_text(raw + "翻动。\n", encoding="utf-8")
    assert importer.apply_plan(importer.build_plan(tmp_path))["updated"] == 1
    with sessions() as session:
        assert session.get(KnowledgeDocument, plan[0]["id"]).content_version == 2


def add_skills(sessions):
    with sessions.begin() as session:
        for name in ("a", "b"):
            session.add(KnowledgeDocument(
                id="skill-" + name, source_id="source", document_type="cooking_skill",
                title="焯水", published=True, content={"markdown": "# 焯水\n冷水下锅。"},
                search_text="焯水 冷水下锅 " + name,
            ))
    with sessions.kw["bind"].connect() as connection:
        rebuild_generation(connection, profile_id="profile")


@pytest.mark.parametrize("mode", ["native", "demo"])
def test_skill_documents_are_distinct_and_isolated(rag_turn, mode):  # noqa: F811
    sessions, _actor = rag_turn
    add_skills(sessions)
    settings = Settings(_env_file=None, retrieval_mode=mode)
    result = gateway.search("焯水", sessions=sessions, settings=settings,
                            document_type="cooking_skill")
    assert set(result.evidence_ids) == {"knowledge:skill-a:v1", "knowledge:skill-b:v1"}
    assert all(e["recipe_id"] is None for e in result.data["evidence"])
    recipes = gateway.search("番茄", sessions=sessions, settings=settings, document_type="recipe")
    assert all(e["kind"] == "recipe" for e in recipes.data["evidence"])
    assert gateway.search("完全不存在的技巧", sessions=sessions, settings=settings,
                          document_type="cooking_skill").status == "no_result"


def test_only_no_result_triggers_web(monkeypatch):
    calls = []

    async def fake_web(query, **kwargs):
        calls.append(query)
        return ToolResult(status="ok", evidence_ids=["web:test"],
                          data={"evidence": [{"id": "web:test", "kind": "web"}]})

    monkeypatch.setattr(graph, "search_web", fake_web)
    for status in ("ok", "unavailable", "no_result"):
        monkeypatch.setattr(graph, "search_retrieval",
                            lambda *a, status=status, **kw: ToolResult(status=status))
        result = asyncio.run(graph.Runtime(None, None).search_cooking_skills(
            QueryInput(query="焯水")))
        assert result.status == ("ok" if status == "no_result" else status)
    assert calls == ["焯水"]


HTML = '''<a class="result__a" href="https://example.org/skill">焯水<b>方法</b></a>
<a class="result__snippet">冷水 &amp; 热水</a>'''


def test_search_parser_stable_identity():
    first, second = web.parse_results(HTML), web.parse_results(HTML)
    assert first[0]["id"] == second[0]["id"]
    assert first[0]["title"] == "焯水方法"
    assert first[0]["content"]["snippet"] == "冷水 & 热水"
    assert first[0]["content"]["evidence_type"] == "search_snippet"


@pytest.mark.parametrize("outcome,code", [
    (202, "WEB_SEARCH_BLOCKED"), (500, "WEB_SEARCH_FAILED"),
    ("timeout", "WEB_SEARCH_FAILED"), ("unknown", "WEB_SEARCH_UNPARSEABLE"),
    ("ok", None), ("empty", None),
])
def test_web_search_outcomes(monkeypatch, outcome, code):
    async def get(self, url, **kwargs):
        if outcome == "timeout":
            raise httpx.ReadTimeout("timeout")
        body = HTML if outcome == "ok" else (
            '<div class="result--no-result">' if outcome == "empty" else "challenge")
        return httpx.Response(outcome if isinstance(outcome, int) else 200,
                              text=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    result = asyncio.run(web.search_web("焯水", settings=Settings(_env_file=None)))
    assert result.error_code == code
    assert result.status == ("unavailable" if code else "ok" if outcome == "ok" else "no_result")


def test_web_disabled():
    result = asyncio.run(web.search_web("焯水", settings=Settings(
        _env_file=None, knowledge_web_search_enabled=False)))
    assert result.error_code == "WEB_SEARCH_DISABLED"


@pytest.mark.parametrize("cite,network", [(True, False), (False, False), (True, True)])
def test_skill_answer_citation(rag_turn, monkeypatch, cite, network):  # noqa: F811
    from test_durable_graph import start

    sessions, actor = rag_turn
    add_skills(sessions)
    if network:
        async def fake_web(query, **kwargs):
            items = web.parse_results(HTML)
            return ToolResult(status="ok", data={"evidence": items},
                              evidence_ids=[e["id"] for e in items])

        monkeypatch.setattr(graph, "search_web", fake_web)

    class Provider:
        async def chat_with_tools(self, *, messages, tools):
            results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
            if not results:
                return {"tool_calls": [{"id": "skill", "type": "function", "function": {
                    "name": "search_cooking_skills", "arguments": json.dumps({
                        "query": "完全不存在的技巧" if network else "焯水"}),
                }}]}
            evidence = results[-1]["evidence_ids"][0]
            return {"content": "冷水下锅。" + (f"[{evidence}]" if cite else "")}

    _, _, snapshot = start(sessions, actor=actor, message="焯水")
    result = graph.compute(sessions, snapshot, Provider())
    assert result["status"] == ("succeeded" if cite else "failed")
    if cite:
        assert result["citations"][0].startswith("web:" if network else "knowledge:")
    else:
        assert result["error_code"] == "MISSING_KNOWLEDGE_CITATION"
