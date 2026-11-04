from datetime import timedelta

import pytest
from argon2 import PasswordHasher
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import User, WorkspaceMember
from app.db.session import SessionLocal
from app.db.v2_models import BackgroundJob, HouseholdInvite, IdempotencyRecord, TurnRun
from app.main import app
from app.services import v2_runtime as runtime


def actor():
    with SessionLocal() as session:
        member = session.scalar(select(WorkspaceMember))
        return member.user_id, member.workspace_id


def test_admission_limit_replay_and_release(monkeypatch):
    user, household = actor()
    monkeypatch.setattr(get_settings(), "runtime_household_active_limit", 1)
    first = runtime.create_turn(user, household, None, "hello", idempotency_key="first")
    assert runtime.create_turn(user, household, None, "hello", idempotency_key="first") == first
    with pytest.raises(HTTPException) as error:
        runtime.create_turn(user, household, None, "second", idempotency_key="second")
    assert error.value.detail["code"] == "HOUSEHOLD_RUN_LIMIT"
    with SessionLocal.begin() as session:
        assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1
        session.get(TurnRun, first["run_id"]).status = "waiting_confirmation"
        session.scalar(select(BackgroundJob)).status = "done"
    runtime.create_turn(user, household, None, "second", idempotency_key="second")


def test_global_queue_full_rolls_back(monkeypatch):
    user, household = actor()
    monkeypatch.setattr(get_settings(), "runtime_queue_capacity", 1)
    runtime.create_turn(user, household, None, "hello")
    another = runtime.create_household(user, "Another")
    with pytest.raises(HTTPException) as error:
        runtime.create_turn(user, another, None, "hello", idempotency_key="rejected")
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "QUEUE_FULL"
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(TurnRun)) == 1
        assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 0


def test_invite_http_requires_csrf_and_is_single_use():
    owner, household = actor()
    token = runtime.create_invite(owner, household)
    with SessionLocal.begin() as session:
        session.add(User(phone="13900000001", display_name="Invited",
                         password_hash=PasswordHasher().hash("test-invite-password")))
    with TestClient(app) as client:
        headers = {"Origin": "http://localhost:5173"}
        assert client.post("/api/v2/invites/accept", json={"token": token},
                           headers=headers).status_code == 401
        login = client.post("/api/v2/auth/login", headers=headers, json={
            "phone": "13900000001", "password": "test-invite-password"})
        assert login.status_code == 200
        assert client.post("/api/v2/invites/accept", json={"token": token},
                           headers=headers).status_code == 403
        headers["X-CSRF-Token"] = login.json()["csrf_token"]
        accepted = client.post("/api/v2/invites/accept", json={"token": token}, headers=headers)
        assert accepted.json() == {"household_id": household}
        assert client.post("/api/v2/invites/accept", json={"token": token},
                           headers=headers).status_code == 410
        assert any(row["id"] == household for row in client.get("/api/v2/households").json())
    expired = runtime.create_invite(owner, household)
    with SessionLocal.begin() as session:
        invite = session.scalar(select(HouseholdInvite).where(
            HouseholdInvite.token_hash == runtime.digest(expired)))
        invite.expires_at = runtime.utc_now() - timedelta(seconds=1)
    with pytest.raises(HTTPException) as error:
        runtime.accept_invite(owner, expired)
    assert error.value.status_code == 410


def test_native_bearer_session_supports_reads_writes_and_revocation():
    user, household = actor()
    with SessionLocal.begin() as session:
        phone = "13900000002"
        session.get(User, user).phone = phone
        session.get(User, user).password_hash = PasswordHasher().hash("native-test-password")
    with TestClient(app) as client:
        response = client.post(
            "/api/v2/auth/login",
            headers={"X-MealFlow-Client": "native-app"},
            json={"phone": phone, "password": "native-test-password"},
        )
        assert response.status_code == 200
        result = response.json()
        assert "access_token" in result
        headers = {"Authorization": f"Bearer {result['access_token']}"}
        assert client.get("/api/v2/auth/me", headers=headers).json() == {"user_id": user}
        households = client.get("/api/v2/households", headers=headers).json()
        assert any(row["id"] == household for row in households)
        created = client.post(
            "/api/v2/households", json={"name": "Native home"}, headers=headers
        )
        assert created.status_code == 201
        assert client.post("/api/v2/auth/logout", headers=headers).status_code == 200
        assert client.get("/api/v2/auth/me", headers=headers).status_code == 401


def test_web_session_does_not_accept_bearer_write_without_csrf():
    user, _ = actor()
    with SessionLocal.begin() as session:
        account = session.get(User, user)
        account.password_hash = PasswordHasher().hash("web-test-password")
        phone = account.phone = "13900000003"
    with TestClient(app) as client:
        response = client.post(
            "/api/v2/auth/login",
            json={"phone": phone, "password": "web-test-password"},
        )
        assert response.status_code == 200
        assert "access_token" not in response.json()
        headers = {
            "Authorization": f"Bearer {response.cookies['mealflow_session']}",
            "Origin": "http://localhost:5173",
        }
        rejected = client.post("/api/v2/households", json={"name": "Rejected"}, headers=headers)
        assert rejected.status_code == 403


def test_invalid_password_hash_is_not_credential():
    user, _ = actor()
    with SessionLocal.begin() as session:
        account = session.get(User, user)
        account.password_hash = "invalid-password-hash"
        phone = account.phone = "13900000004"
    with pytest.raises(HTTPException) as error:
        runtime.login(phone, "invalid-password-hash")
    assert error.value.status_code == 401
