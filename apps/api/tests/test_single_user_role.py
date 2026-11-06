import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db.models import User, WorkspaceMember
from app.db.session import SessionLocal
from app.services import v2_runtime as runtime


def test_invited_user_can_invite_but_outsider_cannot():
    with SessionLocal() as session:
        users = session.scalars(select(User)).all()
        creator, invited, outsider = [user.id for user in users[:3]]
    # Invitation targets an account without an active household under the new one-household rule.
    with SessionLocal.begin() as session:
        for membership in session.scalars(select(WorkspaceMember).where(
            WorkspaceMember.user_id == invited, WorkspaceMember.status == "active"
        )):
            membership.status = "inactive"
    household = runtime.create_household(creator, "平等的家庭")
    runtime.accept_invite(invited, runtime.create_invite(creator, household))
    assert runtime.create_invite(invited, household)
    assert next(item for item in runtime.household_list(invited)
                if item["id"] == household)["role"] == "user"
    with SessionLocal() as session:
        assert {member.role for member in session.scalars(
            select(WorkspaceMember).where(WorkspaceMember.workspace_id == household)
        )} == {"user"}
    with pytest.raises(HTTPException) as error:
        runtime.create_invite(outsider, household)
    assert error.value.status_code == 404
