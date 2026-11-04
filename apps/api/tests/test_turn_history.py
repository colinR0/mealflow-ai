import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db.models import WorkspaceMember
from app.db.session import SessionLocal
from app.db.v2_models import TurnRun
from app.services import turn_history
from app.services import v2_runtime as runtime


def actor():
    with SessionLocal() as session:
        member = session.scalar(select(WorkspaceMember))
        return member.user_id, member.workspace_id


def complete(user, household, chat, text):
    response = runtime.create_turn(user, household, chat, text)
    with SessionLocal() as session, session.begin():
        run = session.get(TurnRun, response["run_id"])
        run.status = "succeeded"
        run.result = {"text": f"回复：{text}"}
    return response


def test_history_pages_restore_and_soft_delete_preserves_runs():
    user, household = actor()
    first = complete(user, household, None, "第一问")
    second = complete(user, household, first["session_id"], "第二问")
    chats = turn_history.list_chats(user, household)
    assert chats[0]["title"] == "第一问"
    detail = turn_history.chat_detail(user, household, first["session_id"], limit=1)
    assert detail["has_more"] is True
    assert detail["runs"][0]["run_id"] == second["run_id"]
    older = turn_history.chat_detail(
        user, household, first["session_id"], before=second["run_id"], limit=1
    )
    assert older["runs"][0]["text"] == "第一问"
    turn_history.delete_chat(user, household, first["session_id"])
    assert turn_history.list_chats(user, household) == []
    assert runtime.accessible_run(user, first["run_id"]).result["text"] == "回复：第一问"
    with pytest.raises(HTTPException) as error:
        runtime.create_turn(user, household, first["session_id"], "继续")
    assert error.value.status_code == 404


def test_history_denies_other_accounts_and_running_deletion():
    user, household = actor()
    response = runtime.create_turn(user, household, None, "还在执行")
    with SessionLocal() as session:
        other = session.scalar(select(WorkspaceMember).where(
            WorkspaceMember.user_id != user, WorkspaceMember.workspace_id == household
        ))
        assert other is not None
        other_user = other.user_id
    assert turn_history.list_chats(other_user, household) == []
    with pytest.raises(HTTPException) as error:
        turn_history.chat_detail(other_user, household, response["session_id"])
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        turn_history.delete_chat(user, household, response["session_id"])
    assert error.value.status_code == 409
