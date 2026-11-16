from datetime import UTC, datetime

import pytest
from argon2 import PasswordHasher

from app.core.config import get_settings
from app.db.models import User, Workspace, WorkspaceMember
from app.db.session import SessionLocal
from app.db.v2_models import CookingLogPhoto
from app.services import cooking_logs as service
from app.services import photo_store


def _jpeg() -> bytes:
    app1 = b"Exif\x00\x00secret"
    scan = b"\x01\x01\x00\x00\x3f\x00"
    return (
        b"\xff\xd8"
        + b"\xff\xe1"
        + (len(app1) + 2).to_bytes(2, "big")
        + app1
        + b"\xff\xda"
        + (len(scan) + 2).to_bytes(2, "big")
        + scan
        + b"\x00\x01\x02"
    )


def _chunk(kind: bytes, data: bytes) -> bytes:
    return len(data).to_bytes(4, "big") + kind + data + b"\x00" * 4


def _png() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", b"\x00" * 13)
        + _chunk(b"eXIf", b"secret")
        + _chunk(b"IEND", b"")
    )


def _actor(session, phone: str, name: str) -> tuple[str, str]:
    user = User(phone=phone, display_name=name, password_hash=PasswordHasher().hash("pw"))
    workspace = Workspace(name=f"{name}家")
    session.add_all([user, workspace])
    session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id))
    session.commit()
    return user.id, workspace.id


def test_exif_and_png_metadata_are_removed():
    jpeg, content_type, ext = photo_store.sanitize(_jpeg())
    assert content_type == "image/jpeg" and ext == "jpg"
    assert b"Exif" not in jpeg and b"secret" not in jpeg
    assert jpeg.startswith(b"\xff\xd8") and b"\xff\xda" in jpeg

    png, content_type, ext = photo_store.sanitize(_png())
    assert content_type == "image/png" and ext == "png"
    assert b"eXIf" not in png and b"secret" not in png
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_photo_upload_is_creator_only_and_delete_removes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "cooking_photo_dir", str(tmp_path))
    with SessionLocal() as session:
        creator, household = _actor(session, "13800000011", "阿戊")
        member, _ = _actor(session, "13800000012", "阿己")
        log = service.create_log(
            session,
            workspace_id=household,
            creator_id=creator,
            title="带图记录",
            cooked_at=datetime(2026, 11, 11, 12, tzinfo=UTC),
            notes="",
            allow_assistant_citation=False,
            dishes=[{"name": "汤"}],
        )
        photo = service.add_photo(
            session,
            workspace_id=household,
            log_id=log["id"],
            editor_id=creator,
            data=_jpeg(),
        )
        assert photo["version"] == 2
        row = session.get(CookingLogPhoto, photo["id"])
        path = photo_store.path_for(row.storage_key)
        assert path.is_file() and b"Exif" not in path.read_bytes()

        with pytest.raises(service.CookingLogError) as denied:
            service.add_photo(
                session,
                workspace_id=household,
                log_id=log["id"],
                editor_id=member,
                data=_jpeg(),
            )
        assert denied.value.status == 403

        result = service.delete_photo(
            session,
            workspace_id=household,
            log_id=log["id"],
            photo_id=photo["id"],
            editor_id=creator,
        )
        assert result["version"] == 3
        assert not path.exists()