import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db.models import WorkspaceMember
from app.db.session import SessionLocal
from app.db.v2_models import IdempotencyRecord
from app.services import v2_runtime as runtime


def test_failed_turn_does_not_leave_empty_idempotency_reservation():
    with SessionLocal() as session:
        member = session.scalar(select(WorkspaceMember))
        user_id, workspace_id = member.user_id, member.workspace_id
    with pytest.raises(HTTPException) as error:
        runtime.create_turn(
            user_id, workspace_id, "missing", "hello", idempotency_key="failed-key"
        )
    assert error.value.status_code == 404
    with SessionLocal() as session:
        assert session.scalar(select(IdempotencyRecord)) is None
    result = runtime.create_turn(
        user_id, workspace_id, None, "hello", idempotency_key="failed-key"
    )
    assert runtime.create_turn(
        user_id, workspace_id, None, "hello", idempotency_key="failed-key"
    ) == result
