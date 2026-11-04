from fastapi.testclient import TestClient

from app.api.routes.v2 import identity
from app.db.models import User
from app.db.session import SessionLocal
from app.main import app
from app.services.v2_runtime import create_household


def household_actor():
    with SessionLocal.begin() as session:
        user = session.query(User).first()
        if user is None:
            user = User(phone="17737771167", display_name="档案测试用户", password_hash="unused")
            session.add(user)
            session.flush()
        user_id = user.id
    household_id = create_household(user_id, "档案测试家庭")
    return user_id, household_id


def test_household_profile_archive_crud_and_conflict():
    user_id, household_id = household_actor()
    app.dependency_overrides[identity] = lambda: (user_id, "test-session")
    try:
        with TestClient(app) as client:
            created = client.post(
                f"/api/v2/households/{household_id}/profiles",
                json={
                    "name": "小明",
                    "gender": "男",
                    "residency": "resident",
                    "taboo": "不吃香菜",
                    "restrictions": [
                        {"type": "allergy", "ingredient_key": "peanut", "ingredient_name": "花生"},
                        {
                            "type": "dislike",
                            "ingredient_key": "cilantro",
                            "ingredient_name": "香菜",
                        },
                    ],
                    "events": [{
                        "event_type": "感冒",
                        "start_date": "2026-11-04",
                        "end_date": "2026-11-07",
                        "constraint_tags": ["宜清淡", "忌辛辣"],
                        "note": "按家人设置执行",
                    }],
                },
            )
            assert created.status_code == 201
            member = created.json()
            assert member["gender"] == "男"
            assert member["restrictions"][0]["type"] == "allergy"
            assert member["events"][0]["constraint_tags"] == ["宜清淡", "忌辛辣"]

            listed = client.get(f"/api/v2/households/{household_id}/profiles")
            assert any(item["id"] == member["id"] for item in listed.json())

            payload = {
                "name": "小明",
                "gender": "男",
                "residency": "resident",
                "taboo": "不吃香菜和葱",
                "note": "",
                "restrictions": member["restrictions"],
                "events": member["events"],
                "expected_version": member["version"],
            }
            updated = client.patch(
                f"/api/v2/households/{household_id}/profiles/{member['id']}", json=payload
            )
            assert updated.status_code == 200
            assert updated.json()["taboo"] == "不吃香菜和葱"
            assert updated.json()["version"] == member["version"] + 1

            stale = client.patch(
                f"/api/v2/households/{household_id}/profiles/{member['id']}", json=payload
            )
            assert stale.status_code == 409

            removed = client.delete(
                f"/api/v2/households/{household_id}/profiles/{member['id']}",
                params={"expected_version": updated.json()["version"]},
            )
            assert removed.status_code == 204
            assert all(item["id"] != member["id"] for item in client.get(
                f"/api/v2/households/{household_id}/profiles"
            ).json())
    finally:
        app.dependency_overrides.pop(identity, None)


def test_profile_is_scoped_to_authorized_household():
    user_id, household_id = household_actor()
    other_household = create_household(user_id, "另一个家庭")
    app.dependency_overrides[identity] = lambda: (user_id, "test-session")
    try:
        with TestClient(app) as client:
            created = client.post(
                f"/api/v2/households/{household_id}/profiles", json={"name": "家庭成员"}
            ).json()
            response = client.patch(
                f"/api/v2/households/{other_household}/profiles/{created['id']}",
                json={"name": "越权修改", "expected_version": created["version"]},
            )
            assert response.status_code == 404
    finally:
        app.dependency_overrides.pop(identity, None)
