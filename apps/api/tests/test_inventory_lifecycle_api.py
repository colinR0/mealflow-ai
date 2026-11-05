"""Inventory v2 HTTP contracts; the shared fixture confines writes to the test database."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import User, WorkspaceMember
from app.db.session import SessionLocal
from app.db.v2_models import AuthSession, InventoryLot, InventoryMovement
from app.main import app
from app.services import v2_runtime as runtime


@pytest.fixture
def inventory_client():
    with SessionLocal.begin() as session:
        user = User(phone="13900000991", display_name="Inventory owner", password_hash="unused")
        session.add(user)
        session.flush()
        user_id = user.id
        session.add(AuthSession(
            user_id=user_id, token_hash=runtime.digest("inventory-native-token"),
            csrf_hash=runtime.digest("inventory-csrf"), native_client=True,
            expires_at=runtime.utc_now() + timedelta(days=1),
            idle_until=runtime.utc_now() + timedelta(hours=1),
        ))
    household = runtime.create_household(user_id, "Inventory tests")
    with TestClient(app, headers={
        "Authorization": "Bearer inventory-native-token",
    }) as client:
        yield client, household, user_id


def base_path(household):
    return f"/api/v2/households/{household}/inventory-lots"


def create_lot(client, household, **values):
    response = client.post(base_path(household), json={"name": "Test food", **values})
    assert response.status_code == 201, response.text
    return response.json()


def mutate(client, path, action, payload):
    if action == "consume":
        return client.post(path + "/consume", json=payload)
    return client.request("DELETE" if action == "remove" else "PATCH", path, json=payload)


def movement_count(lot_id):
    with SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(InventoryMovement).where(
            InventoryMovement.lot_id == lot_id
        ))


@pytest.mark.parametrize("first_action, terminal", [
    ("consume", "consumed"), ("remove", "removed"),
])
def test_terminal_transitions_are_single_shot(inventory_client, first_action, terminal):
    client, household, _ = inventory_client
    lot = create_lot(client, household)
    path = base_path(household) + "/" + lot["id"]
    assert mutate(client, path, first_action, {"expected_version": 1}).status_code == (
        200 if first_action == "consume" else 204
    )
    current = client.get(path).json()
    assert (current["status"], current["version"]) == (terminal, 2)
    for action in ("consume", "remove"):
        conflict = mutate(client, path, action, {"expected_version": 2})
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "INVENTORY_STATE_CONFLICT"
    assert client.get(path).json() == current
    with SessionLocal() as session:
        movements = session.scalars(select(InventoryMovement).where(
            InventoryMovement.lot_id == lot["id"]
        ).order_by(InventoryMovement.created_at)).all()
        assert [row.kind for row in movements] == ["added", terminal]
        assert movements[-1].before["status"] == "active"
        assert movements[-1].after["status"] == terminal


@pytest.mark.parametrize("action", ["patch", "consume", "remove"])
def test_versions_are_required_and_stale_writes_have_no_effect(inventory_client, action):
    client, household, _ = inventory_client
    lot = create_lot(client, household)
    path = base_path(household) + "/" + lot["id"]
    updated = client.patch(path, json={"expected_version": 1, "notes": "new note"})
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    for payload in ({}, {"expected_version": 0}):
        assert mutate(client, path, action, payload).status_code == 422
    stale = mutate(client, path, action, {"expected_version": 1})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "INVENTORY_VERSION_CONFLICT"
    current = client.get(path).json()
    assert {key: value for key, value in current.items() if key != "confirmed_at"} == {
        key: value for key, value in updated.json().items() if key != "confirmed_at"
    }
    assert datetime.fromisoformat(current["confirmed_at"]) == datetime.fromisoformat(
        updated.json()["confirmed_at"]
    )
    assert movement_count(lot["id"]) == 2


@pytest.mark.parametrize("actions", [
    ("patch", "patch"), ("consume", "remove"), ("patch", "consume"),
])
def test_concurrent_writers_commit_only_one_movement(inventory_client, actions):
    client, household, _ = inventory_client
    lot = create_lot(client, household)
    path = base_path(household) + "/" + lot["id"]
    barrier = Barrier(2, timeout=10)

    def write(action):
        with TestClient(app, headers=dict(client.headers)) as concurrent_client:
            barrier.wait()
            return mutate(client=concurrent_client, path=path, action=action,
                          payload={"expected_version": 1}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(write, actions))
    assert responses.count(409) == 1
    assert sum(status in (200, 204) for status in responses) == 1
    assert client.get(path).json()["version"] == 2
    assert movement_count(lot["id"]) == 2


def test_quantity_validation_and_unknown_normalization(inventory_client):
    client, household, _ = inventory_client
    for values in (
        {"quantity_status": "invalid"}, {"quantity_status": "known"},
        {"quantity_status": "known", "quantity": -1, "unit": "kg"},
        {"quantity_status": "known", "quantity": 1, "unit": " "},
    ):
        assert client.post(base_path(household), json={"name": "Food", **values}).status_code == 422
    lot = create_lot(client, household, quantity_status="unknown", quantity=3, unit="kg")
    assert lot["quantity"] is None and lot["unit"] is None
    path = base_path(household) + "/" + lot["id"]
    for values in (
        {"quantity_status": "known"}, {"quantity_status": "known", "quantity": 1},
        {"quantity_status": "known", "quantity": -1, "unit": "kg"},
        {"quantity_status": "known", "quantity": 1, "unit": ""},
        {"quantity_status": "invalid"}, {"name": None}, {"notes": None},
        {"quantity_status": None},
    ):
        response = client.patch(path, json={"expected_version": 1, **values})
        assert response.status_code == 422, response.text
    assert movement_count(lot["id"]) == 1
    known = client.patch(path, json={
        "expected_version": 1, "quantity_status": "known", "quantity": 0, "unit": "kg",
    })
    assert known.status_code == 200 and float(known.json()["quantity"]) == 0
    for values in ({"quantity": None}, {"unit": None}, {"unit": " "}):
        assert client.patch(path, json={"expected_version": 2, **values}).status_code == 422
    partial = client.patch(path, json={"expected_version": 2, "quantity": 2})
    assert partial.status_code == 200 and partial.json()["unit"] == "kg"
    unknown = client.patch(path, json={"expected_version": 3, "quantity_status": "unknown"})
    assert unknown.status_code == 200
    assert unknown.json()["quantity"] is None and unknown.json()["unit"] is None
    normalized = client.patch(path, json={
        "expected_version": 4, "quantity_status": "unknown", "quantity": 5, "unit": "kg",
    })
    assert normalized.status_code == 200
    assert normalized.json()["quantity"] is None and normalized.json()["unit"] is None
    assert movement_count(lot["id"]) == 5


@pytest.mark.parametrize("length", [80, 81, 120])
def test_display_name_generates_a_stable_bounded_key(inventory_client, length):
    client, household, _ = inventory_client
    name = "菜" * length
    first = create_lot(client, household, name=name)
    second = create_lot(client, household, name=name)
    assert first["name"] == name
    assert 0 < len(first["ingredient_key"]) <= 80
    assert first["ingredient_key"] == second["ingredient_key"]
    different = create_lot(client, household, name=name[:-1] + "肉")
    assert different["ingredient_key"] != first["ingredient_key"]
    explicit = create_lot(client, household, name=name, ingredient_key="custom-key")
    assert explicit["ingredient_key"] == "custom-key"
    assert client.post(base_path(household), json={"name": "菜" * 121}).status_code == 422


@pytest.mark.parametrize("quantity", ["999999999.999", "0.001", "1.2300"])
def test_quantity_decimal_boundaries_round_trip_exactly(inventory_client, quantity):
    client, household, _ = inventory_client
    lot = create_lot(client, household, quantity_status="known", quantity=quantity, unit="kg")
    assert Decimal(lot["quantity"]) == Decimal(quantity)
    target = create_lot(client, household)
    path = base_path(household) + "/" + target["id"]
    patched = client.patch(path, json={
        "expected_version": 1, "quantity_status": "known", "quantity": quantity, "unit": "kg",
    })
    assert patched.status_code == 200, patched.text
    assert Decimal(patched.json()["quantity"]) == Decimal(quantity)
    assert Decimal(client.get(path).json()["quantity"]) == Decimal(quantity)


@pytest.mark.parametrize("quantity", [
    "1000000000", 1000000000, "999999999.9991", "1.2345", 1.2345,
    "0.0001", "-0.001", "NaN", "Infinity",
])
def test_invalid_quantity_is_rejected_without_writes(inventory_client, quantity):
    client, household, _ = inventory_client
    lot = create_lot(client, household, quantity_status="known", quantity="2.125", unit="kg")
    path = base_path(household) + "/" + lot["id"]
    values = {"quantity_status": "known", "quantity": quantity, "unit": "kg"}
    created = client.post(base_path(household), json={"name": "Invalid", **values})
    assert created.status_code == 422, created.text
    for patch in (values, {"quantity": quantity}):
        response = client.patch(path, json={"expected_version": 1, **patch})
        assert response.status_code == 422, response.text
    current = client.get(path).json()
    assert current["version"] == 1 and current["quantity"] == "2.125"
    assert movement_count(lot["id"]) == 1
    assert client.get(base_path(household)).json()["total"] == 1


def test_pagination_filters_and_full_summary(inventory_client):
    client, household, user = inventory_client
    path = base_path(household)
    active = [create_lot(client, household, name=f"Batch {index}") for index in range(3)]
    known = create_lot(
        client, household, name="Known", quantity_status="known", quantity=1, unit="kg"
    )
    consumed = create_lot(client, household, name="Batch consumed")
    removed = create_lot(client, household, name="Batch removed")
    assert mutate(client, path + "/" + consumed["id"], "consume", {
        "expected_version": 1,
    }).status_code == 200
    assert mutate(client, path + "/" + removed["id"], "remove", {
        "expected_version": 1,
    }).status_code == 204
    other = runtime.create_household(user, "Other inventory")
    create_lot(client, other, name="Batch foreign")
    first = client.get(path, params={"limit": 2}).json()
    assert (first["total"], first["offset"], first["limit"], first["has_more"]) == (4, 0, 2, True)
    second = client.get(path, params={"offset": 2, "limit": 2}).json()
    assert second["total"] == 4 and second["has_more"] is False
    assert {row["id"] for row in first["items"] + second["items"]} == {
        lot["id"] for lot in [*active, known]
    }
    filtered = client.get(path, params={
        "search": " batch ", "quantity_status": "unknown", "limit": 1,
    }).json()
    assert filtered["total"] == 3 and filtered["has_more"] is True
    assert len(filtered["items"]) == 1
    empty = client.get(path, params={"offset": 99}).json()
    assert empty["items"] == [] and empty["total"] == 4 and not empty["has_more"]
    for status, ids in (
        ("consumed", {consumed["id"]}), ("removed", {removed["id"]}),
        ("all", {lot["id"] for lot in [*active, known, consumed, removed]}),
    ):
        page = client.get(path, params={"status": status}).json()
        assert {row["id"] for row in page["items"]} == ids
        assert page["total"] == len(ids)
    for params in ({"quantity_status": "invalid"}, {"quantity_status": ""},
                   {"status": "invalid"}, {"offset": -1}, {"limit": 101}):
        assert client.get(path, params=params).status_code == 422
    assert client.get(f"/api/v2/households/{household}/inventory-summary").json() == {
        "total": 4, "known": 1, "unknown": 3,
    }


def test_inventory_permissions_and_missing_lots(inventory_client):
    client, household, user = inventory_client
    lot = create_lot(client, household)
    path = base_path(household) + "/" + lot["id"]
    with TestClient(app) as anonymous:
        assert anonymous.get(base_path(household)).status_code == 401
        assert anonymous.get(path).status_code == 401
        assert anonymous.get(f"/api/v2/households/{household}/inventory-summary").status_code == 401
        assert anonymous.post(base_path(household), json={"name": "Denied"}).status_code == 401
        for action in ("patch", "consume", "remove"):
            assert mutate(anonymous, path, action, {"expected_version": 1}).status_code == 401
    with SessionLocal.begin() as session:
        outsider = User(display_name="Outsider", password_hash="unused")
        session.add(outsider)
        session.flush()
        outsider_id = outsider.id
    foreign = runtime.create_household(outsider_id, "Foreign household")
    with SessionLocal.begin() as session:
        membership = session.scalar(select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == household, WorkspaceMember.user_id == user
        ))
        membership.status = "inactive"
    denied = base_path(foreign)
    assert client.get(denied).status_code == 404
    assert client.get(denied + "/" + lot["id"]).status_code == 404
    assert client.post(denied, json={"name": "Denied"}).status_code == 404
    assert client.get(f"/api/v2/households/{foreign}/inventory-summary").status_code == 404
    assert client.get(base_path(household)).status_code == 404
    assert client.get(f"/api/v2/households/{household}/inventory-summary").status_code == 404
    assert client.get(path).status_code == 404
    with SessionLocal.begin() as session:
        member = session.scalar(select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == household, WorkspaceMember.user_id == user
        ))
        member.status = "active"
    other = runtime.create_household(user, "Other authorized household")
    for missing_path in (denied + "/" + lot["id"], base_path(other) + "/" + lot["id"],
                         base_path(household) + "/missing"):
        assert client.get(missing_path).status_code == 404
        for action in ("patch", "consume", "remove"):
            assert mutate(client, missing_path, action, {"expected_version": 1}).status_code == 404
    assert movement_count(lot["id"]) == 1
    with SessionLocal() as session:
        assert session.get(InventoryLot, lot["id"]).version == 1


def test_inventory_browser_writes_keep_csrf_boundary(inventory_client):
    _, household, user = inventory_client
    with SessionLocal.begin() as session:
        session.add(AuthSession(
            user_id=user, token_hash=runtime.digest("inventory-browser-token"),
            csrf_hash=runtime.digest("inventory-browser-csrf"), native_client=False,
            expires_at=runtime.utc_now() + timedelta(days=1),
            idle_until=runtime.utc_now() + timedelta(hours=1),
        ))
    with TestClient(app) as browser:
        browser.cookies.set("mealflow_session", "inventory-browser-token")
        path = base_path(household)
        assert browser.post(path, json={"name": "Denied"}).status_code == 403
        created = browser.post(path, json={"name": "Browser food"}, headers={
            "X-CSRF-Token": "inventory-browser-csrf",
        })
        assert created.status_code == 201
        lot_path = path + "/" + created.json()["id"]
        for action in ("patch", "consume", "remove"):
            assert mutate(browser, lot_path, action, {"expected_version": 1}).status_code == 403
        assert browser.post(path, json={"name": "Denied"}, headers={
            "Authorization": "Bearer inventory-browser-token",
        }).status_code == 403
        assert movement_count(created.json()["id"]) == 1
