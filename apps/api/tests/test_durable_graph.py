"""Runtime tests exercise the actual graph, tools and worker business boundary."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text

from app import worker
from app.agent import durable_graph as graph
from app.db.models import Recipe, WorkspaceMember
from app.db.session import SessionLocal
from app.db.v2_models import (
    BackgroundJob,
    HouseholdProfile,
    KnowledgeDocument,
    KnowledgeSource,
    ShoppingItem,
    ToolExecution,
    TurnRun,
)
from app.services import v2_runtime as runtime


def start(sessions=SessionLocal, *, chat_id=None, message="冰箱还有什么", actor=None):
    if actor is None:
        with sessions() as session:
            member = session.scalar(select(WorkspaceMember))
            actor = member.user_id, member.workspace_id
    response = runtime.create_turn(*actor, chat_id, message)
    job_id = worker.claim("graph-worker")
    snapshot = worker.snapshot_job(job_id, "graph-worker")
    return actor, response, snapshot


def seed_recipes(sessions, household):
    with sessions.begin() as session:
        session.add(
            HouseholdProfile(
                workspace_id=household, display_name="child", restrictions=["花生"], preferences=[]
            )
        )
        source = KnowledgeSource(name="approved test", approval="approved", license_note="test")
        session.add(source)
        session.flush()
        for index in range(5):
            recipe = Recipe(name=f"test-{index}", servings=3, duration_minutes=15)
            session.add(recipe)
            session.flush()
            session.add(
                KnowledgeDocument(
                    recipe_id=recipe.id,
                    source_id=source.id,
                    published=True,
                    content_version=1,
                    capabilities={"steps": True},
                    content={
                        "name": recipe.name,
                        "ingredients": ["花生" if index == 0 else "番茄"],
                        "steps": ["煮熟"],
                    },
                    search_text=recipe.name,
                )
            )


def test_graph_real_tools_and_confirmation_boundary():
    actor, response, snapshot = start(message="买点鸡蛋")
    worker.execute(snapshot.job_id, "graph-worker")
    run = runtime.accessible_run(actor[0], response["run_id"])
    assert run.status == "waiting_confirmation"
    assert "尚未保存" in run.result["text"]
    assert run.state["budgets"] == {"tool_calls": 2, "model_calls": 3}
    assert "execute_tool" in run.state["trace"]
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ToolExecution)) == 2
        assert session.scalar(select(func.count()).select_from(ShoppingItem)) == 0
    action_id = run.result["pending_action"]["id"]
    result = runtime.decide_action(actor[0], action_id, True, "confirm-graph")
    assert result["status"] == "confirmed"
    assert runtime.decide_action(actor[0], action_id, True, "confirm-graph") == result
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ShoppingItem)) == 1


def test_multi_turn_menu_preserves_constraints_and_other_dishes():
    actor, response, snapshot = start(message="三人菜单，不吃鸡蛋")
    seed_recipes(SessionLocal, actor[1])
    worker.execute(snapshot.job_id, "graph-worker")
    first = runtime.accessible_run(actor[0], response["run_id"])
    assert first.status == "succeeded"
    assert len(first.result["menu_draft"]) == 3
    _, response, snapshot = start(chat_id=first.session_id, message="第二道换一道", actor=actor)
    worker.execute(snapshot.job_id, "graph-worker")
    second = runtime.accessible_run(actor[0], response["run_id"])
    assert second.state["constraints"]["diners"] == 3
    assert second.state["constraints"]["excluded_ingredients"] == ["花生", "鸡蛋"]
    old, new = first.result["menu_draft"], second.result["menu_draft"]
    assert old[0] == new[0] and old[2] == new[2] and old[1] != new[1]
    _, response, snapshot = start(
        chat_id=second.session_id, message="取消本次忌口，两人菜单", actor=actor
    )
    worker.execute(snapshot.job_id, "graph-worker")
    third = runtime.accessible_run(actor[0], response["run_id"])
    assert third.state["constraints"] == {"diners": 2, "excluded_ingredients": ["花生"]}


def test_unknown_citation_and_late_writes_rejected():
    class Fabricating:
        async def chat_with_tools(self, **kwargs):
            return {"content": "引用 [recipe:invented:v1]", "citations": ["recipe:invented:v1"]}

    _, _, snapshot = start()
    result = graph.compute(SessionLocal, snapshot, Fabricating())
    assert result["error_code"] == "INVALID_CITATION"
    with SessionLocal.begin() as session:
        session.get(BackgroundJob, snapshot.job_id).attempts += 1
    with pytest.raises(graph.ExecutionStopped, match="LEASE_LOST"):
        graph.compute(SessionLocal, snapshot)


def test_timeout_is_run_wide_and_not_reset_on_retry():
    actor, response, snapshot = start()
    with SessionLocal.begin() as session:
        session.get(TurnRun, snapshot.run_id).created_at = runtime.utc_now() - timedelta(seconds=61)
    worker.execute(snapshot.job_id, "graph-worker")
    run = runtime.accessible_run(actor[0], response["run_id"])
    assert run.status == "failed" and run.error_code == "RUN_TIMEOUT"
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ToolExecution)) == 0


def test_committed_tool_result_reused_after_graph_node_crash(monkeypatch):
    _, _, snapshot = start()
    instance = graph.Runtime(SessionLocal, snapshot)
    state = instance.context({})
    state.update(instance.decide(state))
    original = graph.dispatch
    calls = []

    async def counted(tool, arguments):
        calls.append(tool.name)
        return await original(tool, arguments)

    monkeypatch.setattr(graph, "dispatch", counted)
    first = instance.execute_tools(state)
    # Simulate death after the tool transaction commits but before its node checkpoint.
    recovered = graph.Runtime(SessionLocal, snapshot).execute_tools(state)
    assert recovered["messages"] == first["messages"]
    assert calls == ["get_member_constraints"]


def test_read_result_reused_across_call_ids_and_revoked_facts_rejected(monkeypatch):
    actor, _, snapshot = start()
    instance = graph.Runtime(SessionLocal, snapshot)
    state = instance.context({})
    state.update(instance.decide(state))
    instance.execute_tools(state)

    async def forbidden(*args):
        pytest.fail("Same read arguments and facts must reuse the committed execution")

    monkeypatch.setattr(graph, "dispatch", forbidden)
    state["calls"][0]["id"] = "another-provider-call-id"
    reused = instance.execute_tools(state)
    assert reused["messages"][-1]["tool_call_id"] == "another-provider-call-id"
    with SessionLocal.begin() as session:
        session.add(
            HouseholdProfile(
                workspace_id=actor[1],
                display_name="new restriction",
                restrictions=["牛奶"],
                preferences=[],
            )
        )
    with pytest.raises(graph.ExecutionStopped, match="FACTS_CHANGED"):
        instance.execute_tools(state)


def test_model_budget_reserved_before_failure_survives_new_runtime():
    class Failing:
        async def chat_with_tools(self, **kwargs):
            raise RuntimeError("offline injected failure")

    _, _, snapshot = start()
    for _ in range(graph.MAX_MODEL_CALLS):
        instance = graph.Runtime(SessionLocal, snapshot, Failing())
        with pytest.raises(RuntimeError, match="offline injected"):
            instance.decide(instance.context({}))
    instance = graph.Runtime(SessionLocal, snapshot, Failing())
    result = instance.decide(instance.context({}))
    assert result["response"]["error_code"] == "BUDGET_EXHAUSTED"


def test_tool_return_after_cancellation_cannot_persist(monkeypatch):
    actor, _, snapshot = start()
    instance = graph.Runtime(SessionLocal, snapshot)
    state = instance.context({})
    state.update(instance.decide(state))

    async def cancel_before_return(*args):
        runtime.cancel_run(actor[0], snapshot.run_id)
        return graph.ToolResult(status="ok")

    monkeypatch.setattr(graph, "dispatch", cancel_before_return)
    with pytest.raises(graph.ExecutionStopped, match="RUN_STOPPED"):
        instance.execute_tools(state)
    with SessionLocal() as session:
        records = session.scalars(select(ToolExecution)).all()
        assert len(records) == 1 and records[0].status == "running" and records[0].result == {}


def test_worker_total_deadline_stops_blocked_computation(monkeypatch):
    from threading import Event

    actor, response, snapshot = start()
    release = Event()

    def blocked(_snapshot):
        release.wait(5)
        return {"status": "succeeded", "action": {"type": "add_procurement", "payload": {}}}

    monkeypatch.setattr(worker, "remaining_seconds", lambda _: 0.03)
    monkeypatch.setattr(worker, "compute_result", blocked)
    try:
        worker.execute(snapshot.job_id, "graph-worker")
        run = runtime.accessible_run(actor[0], response["run_id"])
        assert run.error_code == "RUN_TIMEOUT"
        assert run.status == "failed"
        with SessionLocal() as session:
            assert session.get(BackgroundJob, snapshot.job_id).status == "done"
    finally:
        release.set()


def test_postgres_saver_survives_new_runtime_and_fences_stale_attempt(
    postgres_runtime,  # noqa: F811
    monkeypatch,
):
    engine, sessions, actor = postgres_runtime
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    _, response, snapshot = start(sessions, actor=actor, message="买点鸡蛋")
    original = graph.Runtime.validate
    attempts = []

    def crash(self, state):
        attempts.append(True)
        raise RuntimeError("crash before final checkpoint")

    monkeypatch.setattr(graph.Runtime, "validate", crash)
    with pytest.raises(RuntimeError, match="crash before"):
        graph.compute(sessions, snapshot)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ToolExecution)) == 2
    schema = engine.get_execution_options()["schema_translate_map"][None]
    with engine.connect() as connection:
        assert connection.scalar(text(f'SELECT count(*) FROM "{schema}".checkpoints')) > 0
    monkeypatch.setattr(graph.Runtime, "validate", original)
    with sessions.begin() as session:
        session.get(BackgroundJob, snapshot.job_id).lease_until = runtime.utc_now() - timedelta(
            seconds=1
        )
    assert worker.claim("new-worker") == snapshot.job_id
    fresh = worker.snapshot_job(snapshot.job_id, "new-worker")
    with pytest.raises(graph.ExecutionStopped, match="LEASE_LOST"):
        graph.compute(sessions, snapshot)
    result = graph.compute(sessions, fresh)
    assert result["status"] == "waiting_confirmation"
    assert result["_state"]["budgets"]["tool_calls"] == 2
    worker.finish(fresh, result)
    assert runtime.accessible_run(actor[0], response["run_id"]).status == "waiting_confirmation"
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ToolExecution)) == 2


@pytest.fixture(params=["postgres"])
def graph_database(request, monkeypatch):
    if request.param == "postgres":
        _engine, sessions, actor = request.getfixturevalue("postgres_runtime")
        monkeypatch.setattr(worker, "SessionLocal", sessions)
        return sessions, actor
    with SessionLocal() as session:
        member = session.scalar(select(WorkspaceMember))
        return SessionLocal, (member.user_id, member.workspace_id)


def test_s4_structured_restrictions_shared_menu_and_clear(graph_database):
    sessions, actor = graph_database
    seed_recipes(sessions, actor[1])
    with sessions.begin() as session:
        profile = session.scalar(select(HouseholdProfile))
        profile.restrictions = [
            {"type": "allergy", "ingredient_key": "peanut"},
            {"type": "legacy_free_text", "text": "不吃鸡蛋"},
        ]
        session.add(HouseholdProfile(
            workspace_id=actor[1], display_name="家庭共享限制", linked_user_id=None,
            restrictions=[{"type": "dislike", "ingredient_key": "celery"}],
            preferences=[],
        ))
    _, response, snapshot = start(sessions, actor=actor, message="三人菜单")
    instance = graph.Runtime(sessions, snapshot)
    instance.state = instance.context({})
    with sessions() as session:
        peanut_recipe = next(
            doc.recipe_id for doc in session.scalars(select(KnowledgeDocument))
            if "花生" in doc.content["ingredients"]
        )
    availability = asyncio.run(instance.availability(graph.RecipeInput(recipe_id=peanut_recipe)))
    assert availability.data["conflicts"] == ["peanut"]
    assert availability.data["quantity_sufficient"] is None
    worker.execute(snapshot.job_id, "graph-worker")
    first = runtime.accessible_run(actor[0], response["run_id"])
    assert first.status == "succeeded"
    assert len(first.result["menu_draft"]) == 3
    assert first.state["constraints"]["excluded_ingredients"] == ["celery", "peanut", "鸡蛋"]
    assert all("花生" not in row["content"]["ingredients"] for row in first.result["menu_draft"])
    _, response, snapshot = start(
        sessions, actor=actor, chat_id=first.session_id, message="第二道换一道"
    )
    worker.execute(snapshot.job_id, "graph-worker")
    second = runtime.accessible_run(actor[0], response["run_id"])
    before, after = first.result["menu_draft"], second.result["menu_draft"]
    assert before[0] == after[0] and before[2] == after[2] and before[1] != after[1]
    _, response, snapshot = start(
        sessions, actor=actor, chat_id=second.session_id,
        message="取消本次忌口，两人菜单，花生可以吃",
    )
    worker.execute(snapshot.job_id, "graph-worker")
    third = runtime.accessible_run(actor[0], response["run_id"])
    assert third.state["constraints"] == {
        "diners": 2, "excluded_ingredients": ["celery", "peanut", "鸡蛋"]
    }


def test_s4_events_only_apply_within_inclusive_dates(graph_database, monkeypatch):
    sessions, actor = graph_database
    seed_recipes(sessions, actor[1])
    with sessions.begin() as session:
        profile = session.scalar(select(HouseholdProfile))
        profile.restrictions = [{"type": "allergy", "ingredient_key": "peanut"}]
        profile.legacy_details = {"events": [
            {"id": "active", "event_type": "感冒", "constraint_tags": ["忌辣", "宜清淡"],
             "start_date": "2026-11-01", "end_date": "2026-11-03"},
            {"id": "future", "start_date": "2026-11-05", "end_date": "2026-11-06"},
            {"id": "expired", "start_date": "2026-10-01", "end_date": "2026-10-02"},
        ]}
        for doc in session.scalars(select(KnowledgeDocument)):
            doc.content = {**doc.content, "tags": ["辣"]}
    _, _, snapshot = start(sessions, actor=actor, message="三人菜单")
    instance = graph.Runtime(sessions, snapshot)
    clock = [datetime(2026, 11, 1, tzinfo=UTC)]
    monkeypatch.setattr(graph, "utc_now", lambda: clock[0])
    for day in (1, 3):
        clock[0] = datetime(2026, 11, day, tzinfo=UTC)
        with sessions.begin() as session:
            session.get(TurnRun, snapshot.run_id).created_at = clock[0]
        instance.state = instance.context({})
        members = asyncio.run(instance.members(graph.QueryInput(query="成员限制")))
        assert [e["id"] for e in members.data["profiles"][0]["active_events"]] == ["active"]
        assert asyncio.run(instance.menu(graph.QueryInput(query="三人菜单"))).status == "no_result"
    previous = instance.state
    clock[0] = datetime(2026, 11, 4, tzinfo=UTC)
    with sessions.begin() as session:
        session.get(TurnRun, snapshot.run_id).created_at = clock[0]
    with pytest.raises(graph.ExecutionStopped, match="FACTS_CHANGED"):
        instance.check_facts(previous)
    instance.state = instance.context({})
    assert instance.state["member_context"][0]["active_events"] == []
    assert instance.state["constraints"]["excluded_ingredients"] == ["peanut"]
    assert asyncio.run(instance.menu(graph.QueryInput(query="三人菜单"))).status == "ok"
    with sessions.begin() as session:
        profile = session.scalar(select(HouseholdProfile))
        profile.legacy_details = {"events": [*profile.legacy_details["events"], {
            "id": "new", "start_date": "2026-11-04", "end_date": "2026-11-04",
            "constraint_tags": ["忌辣"],
        }]}
    with pytest.raises(graph.ExecutionStopped, match="FACTS_CHANGED"):
        instance.check_facts(instance.state)
    with sessions() as session:
        profile = session.scalar(select(HouseholdProfile))
        assert profile.restrictions == [{"type": "allergy", "ingredient_key": "peanut"}]
        assert len(profile.legacy_details["events"]) == 4


def test_preference_tool_requires_confirmation_and_preserves_allergy(graph_database):
    sessions, actor = graph_database
    with sessions.begin() as session:
        profile = HouseholdProfile(
            workspace_id=actor[1], display_name="孩子",
            restrictions=[{"type": "allergy", "ingredient_key": "peanut"}], preferences=[],
        )
        session.add(profile)
        session.flush()
        profile_id = profile.id

    class PreferenceProvider:
        async def chat_with_tools(self, *, messages, tools):
            completed = [m["name"] for m in messages if m["role"] == "tool"]
            sequence = [
                "get_member_constraints", "get_preference_summary", "prepare_preference_action"
            ]
            if len(completed) == len(sequence):
                return {"content": "请确认长期偏好", "citations": []}
            name = sequence[len(completed)]
            arguments = {"query": "成员偏好"} if name != "prepare_preference_action" else {
                "profile_id": profile_id, "preference": "喜欢清淡",
            }
            return {"tool_calls": [{"id": name, "type": "function", "function": {
                "name": name, "arguments": json.dumps(arguments),
            }}]}

    _, response, snapshot = start(sessions, actor=actor, message="记住孩子喜欢清淡")
    result = graph.compute(sessions, snapshot, PreferenceProvider())
    assert result["status"] == "waiting_confirmation"
    worker.finish(snapshot, result)
    run = runtime.accessible_run(actor[0], response["run_id"])
    with sessions() as session:
        assert session.get(HouseholdProfile, profile_id).preferences == []
    action_id = run.result["pending_action"]["id"]
    confirmed = runtime.decide_action(actor[0], action_id, True, "preference-confirm")
    assert runtime.decide_action(actor[0], action_id, True, "preference-confirm") == confirmed
    with sessions() as session:
        profile = session.get(HouseholdProfile, profile_id)
        assert profile.preferences == ["喜欢清淡"]
        assert profile.restrictions == [{"type": "allergy", "ingredient_key": "peanut"}]
        assert profile.version == 2


@pytest.mark.parametrize(
    "term,ingredient", [("peanut", "花生"), ("鸡蛋", "egg"), ("celery", "芹菜")]
)
def test_availability_and_menu_share_ingredient_matching(term, ingredient):
    state = {"constraints": {"excluded_ingredients": [term]},
             "ingredient_names": {"celery": "芹菜"}}
    assert graph.recipe_conflicts({"ingredients": [ingredient]}, state) == [term]


# Reuse the explicit opt-in, unique-schema PostgreSQL fixture; no app schema is touched.
from test_runtime_postgres_concurrency import postgres_runtime  # noqa: E402, F401
