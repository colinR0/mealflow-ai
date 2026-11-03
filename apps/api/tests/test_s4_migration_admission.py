from datetime import date, timedelta
from decimal import Decimal

import pytest
from argon2 import PasswordHasher
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import User, WorkspaceMember
from app.db.session import SessionLocal
from app.db.v2_models import (
    BackgroundJob,
    HouseholdInvite,
    HouseholdProfile,
    IdempotencyRecord,
    InventoryLot,
    InventoryMovement,
    LegacyImport,
    TurnRun,
)
from app.main import app
from app.services import legacy_migration
from app.services import v2_runtime as runtime


def actor():
    with SessionLocal() as session:
        member = session.scalar(select(WorkspaceMember))
        return member.user_id, member.workspace_id


def source_rows():
    return {
        "users": [{"id": "child", "display_name": "孩子", "gender": "female",
                   "residency": "resident", "taboo": "不吃花生", "note": "备注"}],
        "dietary_restrictions": [
            {"id": "r1", "user_id": "child", "type": "allergy", "ingredient_key": "peanut"},
            {"id": "r2", "user_id": None, "type": "dislike", "ingredient_key": "celery"},
        ],
        "member_food_preferences": [{"id": "p1", "user_id": "child", "target_type": "dish",
                                     "target_key": "soup", "preference": "like"}],
        "member_events": [{"id": "e1", "user_id": "child", "event_type": "感冒",
                           "start_date": "2026-11-01", "end_date": "2026-11-03"}],
        "inventory_items": [{"id": "lot1", "ingredient_key": "egg", "name": "鸡蛋",
                             "quantity": "2.500", "unit": "个", "expires_on": "2026-12-01",
                             "updated_at": "2026-11-01T10:00:00+00:00"}],
    }


def test_import_idempotent_reconciliation_and_no_credentials():
    user, household = actor()
    rows = source_rows()
    kwargs = dict(source="legacy-backup-1", workspace_id=household, actor_id=user)
    with SessionLocal.begin() as session:
        report = legacy_migration.migrate(session, rows, **kwargs)
        assert report["created"] == 3 and not report["issues"]
    with SessionLocal.begin() as session:
        assert legacy_migration.migrate(session, rows, **kwargs)["matched"] == 3
        assert legacy_migration.migrate(session, rows, reconcile=True, **kwargs)["matched"] == 3
        profile = session.scalar(select(HouseholdProfile).where(
            HouseholdProfile.legacy_user_id == "child"))
        assert profile.linked_user_id is None
        assert profile.legacy_details["events"] == rows["member_events"]
        assert {r.get("ingredient_key") for r in profile.restrictions} >= {"peanut", "celery"}
        lot = session.scalar(select(InventoryLot))
        assert lot.quantity == Decimal("2.500") and lot.unit == "个"
        assert lot.expires_on == date(2026, 12, 1)
        assert session.scalar(select(func.count()).select_from(InventoryMovement)) == 1
        assert session.scalar(select(func.count()).select_from(LegacyImport)) == 3
        assert session.scalar(select(User).where(User.id == "child")) is None
        lot.quantity = Decimal("1")
    rows["users"][0]["note"] = "changed"
    with SessionLocal.begin() as session:
        issues = legacy_migration.migrate(session, rows, reconcile=True, **kwargs)["issues"]
        assert {r["reason"] for r in issues} == {"target_drift", "source_changed"}


def test_import_rollback_and_unmapped_rows():
    user, household = actor()
    rows = source_rows()
    kwargs = dict(source="backup", workspace_id=household, actor_id=user)
    with pytest.raises(RuntimeError), SessionLocal.begin() as session:
        legacy_migration.migrate(session, rows, **kwargs)
        raise RuntimeError("fail transaction")
    with SessionLocal.begin() as session:
        assert session.scalar(select(func.count()).select_from(InventoryLot)) == 0
        rows["dietary_restrictions"].append(
            {"id": "orphan", "user_id": "missing", "type": "allergy", "ingredient_key": "milk"})
        rows["inventory_items"][0]["quantity"] = "-1"
        report = legacy_migration.migrate(session, rows, **kwargs)
        assert {r["reason"] for r in report["issues"]} == {"unmapped_member", "invalid_quantity"}


def test_sqlite_source_is_readonly_and_does_not_copy_password(tmp_path):
    import hashlib
    import sqlite3

    from sqlalchemy import create_engine

    from app.db.base import Base

    path = tmp_path / "original.sqlite3"
    engine = create_engine("sqlite:///" + path.as_posix())
    Base.metadata.create_all(engine)
    engine.dispose()
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO workspaces VALUES "
                   "('home','Original','personal','2026-01-01','2026-01-01')")
        db.execute("INSERT INTO users "
                   "(id,email,password_hash,display_name,residency,created_at,updated_at) "
                   "VALUES ('child','child@example.test','plaintext-legacy','Child','resident',"
                   "'2026-01-01','2026-01-01')")
        db.execute("INSERT INTO workspace_members "
                   "(id,workspace_id,user_id,role,status,created_at,updated_at) "
                   "VALUES ('member','home','child','member','active','2026-01-01','2026-01-01')")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    rows = legacy_migration.read_source(path, "home")
    assert rows["users"][0]["id"] == "child"
    assert "password_hash" not in rows["users"][0]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


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
        user = User(email="invited@example.test", display_name="Invited",
                    password_hash=PasswordHasher().hash("test-invite-password"))
        session.add(user)
    with TestClient(app) as client:
        headers = {"Origin": "http://localhost:5173"}
        assert client.post("/api/v2/invites/accept", json={"token": token},
                           headers=headers).status_code == 401
        login = client.post("/api/v2/auth/login", headers=headers, json={
            "email": "invited@example.test", "password": "test-invite-password"})
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


def test_legacy_password_is_not_v2_credential():
    user, _ = actor()
    with SessionLocal.begin() as session:
        account = session.get(User, user)
        account.password_hash = "legacy-password"
        email = account.email
    with pytest.raises(HTTPException) as error:
        runtime.login(email, "legacy-password")
    assert error.value.status_code == 401
