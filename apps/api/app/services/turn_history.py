"""Account-owned V2 conversations. Hide deleted chats without removing audit records."""

from fastapi import HTTPException
from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.db.v2_models import TurnRun, TurnSession
from app.services.v2_runtime import authorized_workspace, utc_now


def owned_chat(session, user_id, household_id, session_id):
    authorized_workspace(session, user_id, household_id)
    chat = session.get(TurnSession, session_id, with_for_update=True)
    if (
        chat is None or chat.workspace_id != household_id or chat.created_by != user_id
        or chat.state.get("deleted_at")
    ):
        raise HTTPException(404, detail={"code": "SESSION_NOT_FOUND"})
    return chat


def list_chats(user_id, household_id, offset=0, limit=30):
    with SessionLocal() as session:
        authorized_workspace(session, user_id, household_id)
        latest = select(
            TurnRun.session_id, func.max(TurnRun.created_at).label("latest_at")
        ).group_by(TurnRun.session_id).subquery()
        chats = session.scalars(
            select(TurnSession).join(latest, latest.c.session_id == TurnSession.id)
            .where(
                TurnSession.workspace_id == household_id,
                TurnSession.created_by == user_id,
                TurnSession.state["deleted_at"].as_string().is_(None),
            )
            .order_by(latest.c.latest_at.desc(), TurnSession.id.desc())
            .offset(offset).limit(limit)
        ).all()
        result = []
        for chat in chats:
            first = session.scalar(select(TurnRun).where(TurnRun.session_id == chat.id)
                                   .order_by(TurnRun.created_at, TurnRun.id).limit(1))
            last = session.scalar(select(TurnRun).where(TurnRun.session_id == chat.id)
                                  .order_by(TurnRun.created_at.desc(), TurnRun.id.desc()).limit(1))
            result.append({
                "id": chat.id, "title": first.request["text"][:60],
                "updated_at": last.created_at.isoformat(), "status": last.status,
            })
        return result


def chat_detail(user_id, household_id, session_id, before=None, limit=30):
    with SessionLocal() as session, session.begin():
        chat = owned_chat(session, user_id, household_id, session_id)
        query = select(TurnRun).where(TurnRun.session_id == chat.id)
        if before:
            anchor = session.get(TurnRun, before)
            if anchor is None or anchor.session_id != chat.id:
                raise HTTPException(400, detail={"code": "INVALID_HISTORY_CURSOR"})
            query = query.where(
                (TurnRun.created_at < anchor.created_at)
                | ((TurnRun.created_at == anchor.created_at) & (TurnRun.id < anchor.id))
            )
        rows = session.scalars(query.order_by(
            TurnRun.created_at.desc(), TurnRun.id.desc()
        ).limit(limit + 1)).all()
        more = len(rows) > limit
        rows = list(reversed(rows[:limit]))
        return {
            "session_id": chat.id, "has_more": more,
            "runs": [{
                "run_id": row.id, "text": row.request["text"], "status": row.status,
                "result": row.result, "error_code": row.error_code,
                "created_at": row.created_at.isoformat(),
            } for row in rows],
        }


def delete_chat(user_id, household_id, session_id):
    with SessionLocal() as session, session.begin():
        chat = owned_chat(session, user_id, household_id, session_id)
        active = session.scalar(select(TurnRun.id).where(
            TurnRun.session_id == chat.id, TurnRun.status.in_(["queued", "running"])
        ).limit(1))
        if active:
            raise HTTPException(409, detail={"code": "RUN_BUSY"})
        chat.state = {**chat.state, "deleted_at": utc_now().isoformat()}
        return {"status": "deleted"}
