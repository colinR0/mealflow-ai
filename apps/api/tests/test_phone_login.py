from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from app.db.models import User
from app.db.session import SessionLocal
from app.main import app
from app.services.v2_runtime import create_household


def test_phone_only_login_and_household_access():
    with SessionLocal.begin() as session:
        user = User(phone="17737771167", display_name="手机用户",
                    password_hash=PasswordHasher().hash("123456"))
        session.add(user)
        session.flush()
        user_id = user.id
    household = create_household(user_id, "我的家庭")
    with TestClient(app) as client:
        assert client.post("/api/v2/auth/login", json={
            "email": "legacy@example.test", "password": "123456"
        }).status_code == 422
        assert client.post("/api/v2/auth/login", json={
            "phone": "legacy@example.test", "password": "123456"
        }).status_code == 422
        assert client.post("/api/v2/auth/login", json={
            "phone": "17737771167", "password": "incorrect"
        }).status_code == 401
        result = client.post("/api/v2/auth/login",
                             headers={"X-MealFlow-Client": "native-app"},
                             json={"phone": "17737771167", "password": "123456"})
        assert result.status_code == 200
        headers = {"Authorization": f"Bearer {result.json()['access_token']}"}
        assert client.get("/api/v2/households", headers=headers).json() == [
            {"id": household, "name": "我的家庭", "role": "user"}
        ]
