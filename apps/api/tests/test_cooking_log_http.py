from datetime import UTC, datetime

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from app.db.models import User, Workspace, WorkspaceMember
from app.db.session import SessionLocal
from app.main import app

PASSWORD = "test-cooking-password"


def _household(*phones: str) -> str:
    with SessionLocal() as session:
        workspace = Workspace(name="做饭记录测试家")
        session.add(workspace)
        session.flush()
        for index, phone in enumerate(phones):
            user = User(
                phone=phone,
                display_name=f"成员{index}",
                password_hash=PasswordHasher().hash(PASSWORD),
            )
            session.add(user)
            session.flush()
            session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id))
        session.commit()
        return workspace.id


def _login(client: TestClient, phone: str) -> dict[str, str]:
    response = client.post(
        "/api/v2/auth/login",
        headers={"X-MealFlow-Client": "native-app"},
        json={"phone": phone, "password": PASSWORD},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_cooking_log_http_permissions_and_comments():
    household = _household("13900000021", "13900000022")
    with TestClient(app) as client:
        assert client.get(f"/api/v2/households/{household}/cooking-logs").status_code == 401
        creator = _login(client, "13900000021")
        member = _login(client, "13900000022")
        payload = {
            "title": "清蒸鱼",
            "cooked_at": datetime(2026, 11, 11, 11, tzinfo=UTC).isoformat(),
            "notes": "火候刚好",
            "allow_assistant_citation": False,
            "dishes": [{"name": "清蒸鱼"}, {"name": "米饭"}],
        }
        created = client.post(
            f"/api/v2/households/{household}/cooking-logs",
            headers=creator,
            json=payload,
        )
        assert created.status_code == 201
        log = created.json()
        assert log["version"] == 1 and log["can_edit"] is True

        listed = client.get(
            f"/api/v2/households/{household}/cooking-logs", headers=member
        )
        assert listed.status_code == 200
        assert listed.json()["items"][0]["can_edit"] is False

        comment = client.post(
            f"/api/v2/households/{household}/cooking-logs/{log['id']}/comments",
            headers=member,
            json={"body": "下次少放盐"},
        )
        assert comment.status_code == 201
        assert comment.json()["author"]["display_name"] == "成员1"

        denied = client.patch(
            f"/api/v2/households/{household}/cooking-logs/{log['id']}",
            headers=member,
            json={"expected_version": 1, "title": "改标题"},
        )
        assert denied.status_code == 403
        assert denied.json()["detail"]["code"] == "CREATOR_ONLY"

        stale = client.patch(
            f"/api/v2/households/{household}/cooking-logs/{log['id']}",
            headers=creator,
            json={"expected_version": 9, "notes": "旧版本"},
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["current_version"] == 1

        updated = client.patch(
            f"/api/v2/households/{household}/cooking-logs/{log['id']}",
            headers=creator,
            json={"expected_version": 1, "allow_assistant_citation": True},
        )
        assert updated.status_code == 200
        assert updated.json()["version"] == 2
        assert updated.json()["allow_assistant_citation"] is True

        detail = client.get(
            f"/api/v2/households/{household}/cooking-logs/{log['id']}", headers=member
        )
        assert detail.status_code == 200
        assert detail.json()["comment_count"] == 1


def _jpeg() -> bytes:
    app1 = b"Exif\x00\x00secret"
    scan = b"\x01\x01\x00\x00\x3f\x00"
    return (
        b"\xff\xd8\xff\xe1"
        + (len(app1) + 2).to_bytes(2, "big")
        + app1
        + b"\xff\xda"
        + (len(scan) + 2).to_bytes(2, "big")
        + scan
        + b"\x00\x01\x02"
    )


def test_cooking_photo_http_is_creator_only_and_strips_metadata(tmp_path, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "cooking_photo_dir", str(tmp_path))
    household = _household("13900000023", "13900000024")
    with TestClient(app) as client:
        creator = _login(client, "13900000023")
        member = _login(client, "13900000024")
        created = client.post(
            f"/api/v2/households/{household}/cooking-logs",
            headers=creator,
            json={
                "title": "带照片",
                "cooked_at": datetime(2026, 11, 11, 12, tzinfo=UTC).isoformat(),
                "dishes": [{"name": "汤"}],
            },
        )
        log_id = created.json()["id"]
        photo_url = (
            f"/api/v2/households/{household}/cooking-logs/{log_id}/photos"
        )
        denied = client.post(
            photo_url,
            headers={**member, "Content-Type": "image/jpeg"},
            content=_jpeg(),
        )
        assert denied.status_code == 403

        uploaded = client.post(
            photo_url,
            headers={**creator, "Content-Type": "image/jpeg"},
            content=_jpeg(),
        )
        assert uploaded.status_code == 201
        photo = uploaded.json()
        assert photo["version"] == 2
        served = client.get(
            f"{photo_url}/{photo['id']}", headers=member
        )
        assert served.status_code == 200
        assert served.headers["content-type"] == "image/jpeg"
        assert b"Exif" not in served.content and b"secret" not in served.content
        with TestClient(app) as anonymous:
            assert anonymous.get(f"{photo_url}/{photo['id']}").status_code == 401