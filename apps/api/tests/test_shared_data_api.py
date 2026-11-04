from fastapi.testclient import TestClient

from app.main import app


def test_shared_procurement_crud() -> None:
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/workspaces/home-workspace/procurement-items",
            json={"ingredient": "番茄", "quantity": "2", "unit": "个", "source": "manual"},
        )
        assert created.status_code == 201
        item = created.json()
        assert item["ingredient"] == "番茄"

        updated = client.patch(
            f"/api/v1/procurement-items/{item['id']}",
            json={"checked": True},
        )
        assert updated.status_code == 200
        assert updated.json()["checked"] is True

        listed = client.get("/api/v1/workspaces/home-workspace/procurement-items")
        assert listed.status_code == 200
        assert listed.json()[0]["checked"] is True

        deleted = client.delete(f"/api/v1/procurement-items/{item['id']}")
        assert deleted.status_code == 204


def test_shared_cooking_record_crud() -> None:
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/workspaces/home-workspace/cooking-records",
            json={
                "dish_name": "番茄炒蛋",
                "cook": "我",
                "cooked_at": "2026-10-06T18:30",
                "servings": 2,
                "duration_minutes": 15,
                "difficulty": "快手",
                "tags": ["家常"],
                "ingredients": "番茄、鸡蛋",
                "story": "测试记录",
                "steps": [{"title": "炒制", "detail": "翻炒", "minutes": 10}],
                "rating": 4.5,
                "next_time_note": "少放盐",
            },
        )
        assert created.status_code == 201
        record = created.json()
        assert record["dish_name"] == "番茄炒蛋"

        listed = client.get("/api/v1/workspaces/home-workspace/cooking-records")
        assert listed.status_code == 200
        assert listed.json()[0]["id"] == record["id"]

        updated = client.patch(
            f"/api/v1/cooking-records/{record['id']}",
            json={"tags": None, "steps": None, "reviews": None},
        )
        assert updated.status_code == 200
        for field in ("tags", "steps", "reviews"):
            assert updated.json()[field] == []

        listed = client.get("/api/v1/workspaces/home-workspace/cooking-records")
        assert listed.status_code == 200
        for field in ("tags", "steps", "reviews"):
            assert listed.json()[0][field] == []

        deleted = client.delete(f"/api/v1/cooking-records/{record['id']}")
        assert deleted.status_code == 204
