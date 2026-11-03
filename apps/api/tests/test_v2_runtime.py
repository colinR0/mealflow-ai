from datetime import timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.routes.v2 import identity
from app.db.models import WorkspaceMember
from app.db.session import SessionLocal
from app.db.v2_models import AgentEvent, BackgroundJob, PendingAction, ShoppingItem
from app.main import app
from app.services import v2_runtime as runtime
from app.services.hybrid_retrieval import RetrievalHit, cosine, reciprocal_rank_fusion
from app.worker import claim, execute, handle_failure


def actor():
    with SessionLocal() as session:
        member = session.scalar(select(WorkspaceMember))
        return member.user_id, member.workspace_id


def test_worker_action_is_preview_then_confirmed_once():
    user, household = actor()
    run = runtime.create_turn(user, household, None, "买点鸡蛋")
    execute(claim("worker-a"), "worker-a")
    completed = runtime.accessible_run(user, run["run_id"])
    assert completed.status == "waiting_confirmation"
    action = completed.result["pending_action"]
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ShoppingItem)) == 0
    first = runtime.decide_action(user, action["id"], True, "key")
    assert runtime.decide_action(user, action["id"], True, "key") == first
    with SessionLocal() as session:
        item = session.scalar(select(ShoppingItem))
        assert item.quantity is None and item.quantity_status == "unknown"
        assert session.scalar(select(func.count()).select_from(ShoppingItem)) == 1
    with pytest.raises(HTTPException) as error:
        runtime.decide_action(user, action["id"], False, "reject")
    assert error.value.status_code == 409


def test_http_decision_resolves_run_restore_and_sse_without_duplicate_events(monkeypatch):
    user, household = actor()
    monkeypatch.setattr(runtime, "authenticate", lambda token: (user, "session"))
    run_data = runtime.create_turn(user, household, None, "买点鸡蛋")
    execute(claim("worker-http"), "worker-http")
    run = runtime.accessible_run(user, run_data["run_id"])
    action = run.result["pending_action"]

    app.dependency_overrides[identity] = lambda: (user, "session")
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v2/actions/{action['id']}/confirm",
                headers={"Origin": "http://testserver", "Idempotency-Key": "http-decision"},
                json={
                    "expected_version": action["version"],
                    "payload_hash": action["payload_hash"],
                },
            )
            assert response.status_code == 200
            first = response.json()
    finally:
        app.dependency_overrides.pop(identity, None)
    resolved = runtime.accessible_run(user, run.id)
    assert resolved.status == "succeeded"
    assert resolved.result["action_result"] == first
    assert "pending_action" not in resolved.result
    assert resolved.state["previous_action_result"] == first
    next_run = runtime.create_turn(user, household, run.session_id, "你好")
    assert runtime.accessible_run(user, next_run["run_id"]).state["previous_action_result"] == first

    with SessionLocal() as session:
        events = session.scalars(
            select(AgentEvent).where(AgentEvent.run_id == run.id).order_by(AgentEvent.seq)
        ).all()
        assert [event.type for event in events].count("action_decided") == 1
        assert session.get(PendingAction, action["id"]).status == "confirmed"

    # A new idempotency key is a replay of the already-decided action, not a
    # second business event.
    assert runtime.decide_action(user, action["id"], True, "http-decision-replay") == first
    with SessionLocal() as session:
        assert session.scalar(
            select(func.count()).select_from(AgentEvent).where(
                AgentEvent.run_id == run.id, AgentEvent.type == "action_decided"
            )
        ) == 1

    app.dependency_overrides[identity] = lambda: (user, "session")
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/api/v2/agent/turn-runs/{run.id}/events",
                headers={"Last-Event-ID": f"{run.id}:2"},
            )
            assert response.status_code == 200
            assert "event: action_decided" in response.text
            assert "event: action_required" in response.text
            restored = client.get(f"/api/v2/agent/turn-runs/{run.id}")
            assert restored.json()["status"] == "succeeded"
            assert "pending_action" not in restored.json()["result"]
    finally:
        app.dependency_overrides.pop(identity, None)


def test_rejected_http_decision_resolves_run_and_is_replayed():
    user, household = actor()
    run_data = runtime.create_turn(user, household, None, "买点鸡蛋")
    execute(claim("worker-reject"), "worker-reject")
    run = runtime.accessible_run(user, run_data["run_id"])
    action = run.result["pending_action"]
    result = runtime.decide_action(user, action["id"], False, "http-reject")
    assert result["status"] == "rejected"
    resolved = runtime.accessible_run(user, run.id)
    assert resolved.status == "succeeded"
    assert resolved.result["action_result"] == result
    assert resolved.state["previous_action_result"]["status"] == "rejected"


def test_run_busy_private_and_idempotent():
    user, household = actor()
    run = runtime.create_turn(user, household, None, "你好", idempotency_key="same")
    assert runtime.create_turn(user, household, None, "你好", idempotency_key="same") == run
    with pytest.raises(HTTPException) as error:
        runtime.create_turn(user, household, run["session_id"], "下一轮")
    assert error.value.status_code == 409
    with pytest.raises(HTTPException):
        runtime.accessible_run("another-user", run["run_id"])
    with pytest.raises(HTTPException):
        runtime.create_turn(user, "another-household", None, "你好")


def test_cancel_discards_output_and_expired_lease_can_be_reclaimed():
    user, household = actor()
    run = runtime.create_turn(user, household, None, "买点鸡蛋")
    job_id = claim("old")
    with SessionLocal() as session, session.begin():
        session.get(BackgroundJob, job_id).lease_until = runtime.utc_now() - timedelta(seconds=1)
    assert claim("new") == job_id
    runtime.cancel_run(user, run["run_id"])
    execute(job_id, "old")
    execute(job_id, "new")
    assert runtime.accessible_run(user, run["run_id"]).status == "cancelled"
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ShoppingItem)) == 0


def test_worker_failure_is_terminal_after_three_attempts():
    user, household = actor()
    run = runtime.create_turn(user, household, None, "你好")
    for _ in range(3):
        job_id = claim(f"worker-{_}")
        handle_failure(job_id, f"worker-{_}")
        if _ < 2:
            with SessionLocal() as session, session.begin():
                session.get(BackgroundJob, job_id).next_run_at = runtime.utc_now()
    failed = runtime.accessible_run(user, run["run_id"])
    assert failed.status == "failed"
    assert failed.error_code == "RETRY_EXHAUSTED"
    with SessionLocal() as session:
        job = session.get(BackgroundJob, job_id)
        assert job.status == "done"


def test_action_stale_invalid_unknown_and_expired():
    user, household = actor()
    with pytest.raises(HTTPException):
        runtime.prepare_action(
            user,
            household,
            "add_inventory",
            {
                "name": "番茄",
                "ingredient_key": "tomato",
                "quantity_status": "unknown",
                "quantity": 0,
            },
        )
    action = runtime.prepare_action(user, household, "add_procurement", {"name": "番茄"})
    with pytest.raises(HTTPException) as error:
        runtime.decide_action(user, action["id"], True, "key", expected_version=2)
    assert error.value.status_code == 409


def test_sse_replay_and_foreign_cursor(monkeypatch):
    user, household = actor()
    monkeypatch.setattr(runtime, "authenticate", lambda token: (user, "session"))
    run = runtime.create_turn(user, household, None, "你好")
    execute(claim("worker"), "worker")
    with TestClient(app) as client:
        url = f"/api/v2/agent/turn-runs/{run['run_id']}/events"
        response = client.get(url, headers={"Last-Event-ID": f"{run['run_id']}:1"})
        assert response.status_code == 200
        assert "event: completed" in response.text
        assert f"id: {run['run_id']}:1\n" not in response.text
        assert client.get(url, headers={"Last-Event-ID": "other:1"}).status_code == 400


def test_sse_closes_after_session_revocation(monkeypatch):
    user, household = actor()
    calls = 0

    def authenticate(_token):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise HTTPException(401, detail={"code": "UNAUTHENTICATED"})
        return user, "session"

    monkeypatch.setattr(runtime, "authenticate", authenticate)
    run = runtime.create_turn(user, household, None, "你好")
    with TestClient(app) as client:
        url = f"/api/v2/agent/turn-runs/{run['run_id']}/events"
        response = client.get(url)
        assert response.status_code == 200
        assert response.text == ""


def test_rrf_parent_dedup_and_cosine_validation():
    a = RetrievalHit("a", "a1", 1, 0.9, "a")
    b = RetrievalHit("a", "a2", 2, 0.8, "a")
    assert reciprocal_rank_fusion([[a, b]]) == [("a", 1 / 61)]
    assert cosine([2, 0], [1, 1]) == pytest.approx(2**-0.5)
    with pytest.raises(ValueError):
        cosine([0], [float("nan")])


def test_cookie_login_csrf_logout_and_rate_limit():
    from argon2 import PasswordHasher

    from app.db.models import User

    user, household = actor()
    with SessionLocal() as session, session.begin():
        account = session.get(User, user)
        account.password_hash = PasswordHasher().hash("local-test-password")
        email = account.email
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v2/auth/login", json={"email": email, "password": "local-test-password"}
            ).status_code
            == 403
        )
        headers = {"Origin": "http://localhost:5173"}
        login = client.post(
            "/api/v2/auth/login",
            headers=headers,
            json={"email": email, "password": "local-test-password"},
        )
        assert login.status_code == 200
        assert client.get("/api/v2/auth/me").status_code == 200
        assert client.post("/api/v2/auth/logout", headers=headers).status_code == 403
        headers["X-CSRF-Token"] = login.json()["csrf_token"]
        assert client.post("/api/v2/auth/logout", headers=headers).status_code == 200
        assert client.get("/api/v2/auth/me").status_code == 401
        for _ in range(5):
            assert (
                client.post(
                    "/api/v2/auth/login",
                    headers=headers,
                    json={"email": email, "password": "wrong"},
                ).status_code
                == 401
            )
        assert (
            client.post(
                "/api/v2/auth/login", headers=headers, json={"email": email, "password": "wrong"}
            ).status_code
            == 429
        )
