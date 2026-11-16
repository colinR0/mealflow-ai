from datetime import UTC, datetime

import pytest
from argon2 import PasswordHasher

from app.db.models import User, Workspace, WorkspaceMember
from app.db.session import SessionLocal
from app.services import cooking_logs as service


def _actor(session, phone: str, name: str) -> tuple[str, str]:
    user = User(phone=phone, display_name=name, password_hash=PasswordHasher().hash("pw"))
    workspace = Workspace(name=f"{name}家")
    session.add_all([user, workspace])
    session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id))
    session.commit()
    return user.id, workspace.id


def test_creator_edits_and_household_comments():
    with SessionLocal() as session:
        creator, household = _actor(session, "13800000001", "阿甲")
        member, _ = _actor(session, "13800000002", "阿乙")
        log = service.create_log(
            session,
            workspace_id=household,
            creator_id=creator,
            title="番茄炒蛋",
            cooked_at=datetime(2026, 11, 10, 12, tzinfo=UTC),
            notes="第一次做",
            allow_assistant_citation=False,
            dishes=[{"name": "番茄炒蛋"}, {"name": "米饭", "note": "两碗"}],
        )
        assert log["version"] == 1 and log["can_edit"] is True
        assert [item["name"] for item in log["dishes"]] == ["番茄炒蛋", "米饭"]

        viewed = service.get_log(session, household, log["id"], member)
        assert viewed["can_edit"] is False
        service.add_comment(
            session, workspace_id=household, log_id=log["id"], author_id=member, body="看着不错"
        )
        detail = service.get_log(session, household, log["id"], creator)
        assert detail["comment_count"] == 1
        assert detail["comments"][0]["author"]["display_name"] == "阿乙"

        with pytest.raises(service.CookingLogError) as denied:
            service.update_log(
                session,
                workspace_id=household,
                log_id=log["id"],
                editor_id=member,
                expected_version=1,
                title="改名",
            )
        assert denied.value.status == 403

        updated = service.update_log(
            session,
            workspace_id=household,
            log_id=log["id"],
            editor_id=creator,
            expected_version=1,
            title="番茄炒蛋（改良）",
            allow_assistant_citation=True,
            dishes=[{"name": "番茄炒蛋"}],
        )
        assert updated["version"] == 2
        assert updated["title"] == "番茄炒蛋（改良）"
        assert [item["name"] for item in updated["dishes"]] == ["番茄炒蛋"]
        assert updated["allow_assistant_citation"] is True

        with pytest.raises(service.CookingLogError) as conflict:
            service.update_log(
                session,
                workspace_id=household,
                log_id=log["id"],
                editor_id=creator,
                expected_version=1,
                notes="过期修改",
            )
        assert conflict.value.status == 409
        assert conflict.value.detail == "2"


def test_citation_is_opt_in_and_mine_filter_works():
    with SessionLocal() as session:
        creator, household = _actor(session, "13800000003", "阿丙")
        member, _ = _actor(session, "13800000004", "阿丁")
        first = service.create_log(
            session,
            workspace_id=household,
            creator_id=creator,
            title="默认不引用",
            cooked_at=datetime(2026, 11, 9, 18, tzinfo=UTC),
            notes="",
            allow_assistant_citation=False,
            dishes=[{"name": "面"}],
        )
        service.create_log(
            session,
            workspace_id=household,
            creator_id=member,
            title="别人做的",
            cooked_at=datetime(2026, 11, 8, 18, tzinfo=UTC),
            notes="",
            allow_assistant_citation=True,
            dishes=[],
        )
        citable = service.citable_logs(session, household)
        assert [item["title"] for item in citable] == ["别人做的"]
        service.update_log(
            session,
            workspace_id=household,
            log_id=first["id"],
            editor_id=creator,
            expected_version=1,
            allow_assistant_citation=True,
        )
        citable = service.citable_logs(session, household)
        assert [item["title"] for item in citable] == ["默认不引用", "别人做的"]
        mine = service.list_logs(
            session, household, member, limit=10, offset=0, creator_id=member
        )
        assert [item["title"] for item in mine] == ["别人做的"]
        assert service.list_logs(session, household, member, limit=10, offset=0)[0][
            "title"
        ] == "默认不引用"