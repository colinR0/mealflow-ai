"""Manual procurement create stays on the disposable test database."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import User
from app.db.session import SessionLocal
from app.db.v2_models import AuthSession, InventoryLot
from app.main import app
from app.services import v2_runtime as runtime


def test_manual_purchase_stays_pending_and_does_not_write_inventory():
    with SessionLocal.begin() as session:
        user = User(phone="13900000992", display_name="Shopping owner", password_hash="unused")
        session.add(user)
        session.flush()
        user_id = user.id
        session.add(AuthSession(
            user_id=user_id, token_hash=runtime.digest("shopping-native-token"),
            csrf_hash=runtime.digest("shopping-csrf"), native_client=True,
            expires_at=runtime.utc_now() + timedelta(days=1),
            idle_until=runtime.utc_now() + timedelta(hours=1),
        ))
    household = runtime.create_household(user_id, "Shopping tests")
    base = f"/api/v2/households/{household}/procurement-items"
    with TestClient(app, headers={"Authorization": "Bearer shopping-native-token"}) as client:
        missing = client.post(
            base, json={"name": "鸡蛋", "quantity_status": "known", "quantity": 2},
        )
        assert missing.status_code == 422

        created = client.post(base, json={"name": "鲜牛奶", "quantity_status": "unknown"})
        assert created.status_code == 201, created.text
        item = created.json()
        assert item["status"] == "pending"
        assert item["quantity_status"] == "unknown"
        assert set(item) == {"id", "name", "quantity_status", "status"}

        listed = client.get(base)
        assert listed.status_code == 200
        assert listed.json()[0]["name"] == "鲜牛奶"
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(InventoryLot).where(
            InventoryLot.workspace_id == household
        )) == 0
