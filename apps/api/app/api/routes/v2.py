"""Authenticated API boundary for browser and native clients."""

import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.db.v2_models import AgentEvent, AuthSession, InventoryLot, ShoppingItem
from app.services import turn_history
from app.services import v2_runtime as runtime
from app.services.household_profiles import (
    ProfileCreate,
    ProfileUpdate,
    create_archive,
    delete_archive,
    list_archives,
    update_archive,
)

router = APIRouter(prefix="/api/v2", tags=["v2"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    phone: str = Field(pattern=r"^1[3-9][0-9]{9}$")
    password: str = Field(min_length=1, max_length=256)


class Name(Input):
    name: str = Field(min_length=1, max_length=80)


class Turn(Input):
    household_id: str
    session_id: str | None = None
    parent_run_id: str | None = None
    text: str = Field(min_length=1, max_length=4000)


class Decision(Input):
    expected_version: int = Field(ge=1)
    payload_hash: str = Field(min_length=64, max_length=64)


class InviteAcceptance(Input):
    token: str = Field(min_length=1, max_length=200)


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin not in get_settings().cors_origins and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, detail={"code": "ORIGIN_REJECTED"})


def identity(request: Request) -> tuple[str, str]:
    authorization = request.headers.get("authorization", "")
    bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    user_id, session_id = runtime.authenticate(
        bearer or request.cookies.get("mealflow_session", "")
    )
    native_bearer = bool(bearer)
    if request.method not in {"GET", "HEAD"}:
        if request.headers.get("origin"):
            check_origin(request)
        if native_bearer:
            with SessionLocal() as session:
                record = session.get(AuthSession, session_id)
                if not record.native_client:
                    raise HTTPException(403, detail={"code": "NATIVE_SESSION_REQUIRED"})
        else:
            import secrets

            with SessionLocal() as session:
                record = session.get(AuthSession, session_id)
                if not secrets.compare_digest(
                    record.csrf_hash, runtime.digest(request.headers.get("x-csrf-token", ""))
                ):
                    raise HTTPException(403, detail={"code": "CSRF_REJECTED"})
    return user_id, session_id


Identity = Annotated[tuple[str, str], Depends(identity)]


@router.post("/auth/login")
def login(body: Login, request: Request, response: Response):
    if request.headers.get("origin"):
        check_origin(request)
    native_client = request.headers.get("x-mealflow-client") == "native-app"
    token, csrf, user_id = runtime.login(
        body.phone, body.password, native_client=native_client
    )
    response.set_cookie(
        "mealflow_session",
        token,
        httponly=True,
        samesite="lax",
        secure=get_settings().app_env != "development" and request.url.scheme == "https",
        max_age=604800,
    )
    result = {"user_id": user_id, "csrf_token": csrf}
    if native_client:
        result["access_token"] = token
    return result


@router.get("/auth/me")
def me(actor: Identity):
    return {"user_id": actor[0]}


@router.post("/auth/logout")
def logout(actor: Identity, response: Response):
    with SessionLocal() as session, session.begin():
        session.get(AuthSession, actor[1]).revoked = True
    response.delete_cookie("mealflow_session")
    return {"status": "logged_out"}


@router.get("/households")
def households(actor: Identity):
    return runtime.household_list(actor[0])


@router.post("/households", status_code=201)
def household(body: Name, actor: Identity):
    return {"id": runtime.create_household(actor[0], body.name)}


@router.post("/households/{household_id}/invites", status_code=201)
def invite(household_id: str, actor: Identity):
    return {"token": runtime.create_invite(actor[0], household_id)}


@router.get("/households/{household_id}/profiles")
def profiles(household_id: str, actor: Identity):
    return list_archives(actor[0], household_id)


@router.post("/invites/accept")
def accept_invite(body: InviteAcceptance, actor: Identity):
    return {"household_id": runtime.accept_invite(actor[0], body.token)}


@router.post("/households/{household_id}/profiles", status_code=201)
def profile(household_id: str, body: ProfileCreate, actor: Identity):
    return create_archive(actor[0], household_id, body)


@router.patch("/households/{household_id}/profiles/{profile_id}")
def edit_profile(household_id: str, profile_id: str, body: ProfileUpdate, actor: Identity):
    return update_archive(actor[0], household_id, profile_id, body)


@router.delete("/households/{household_id}/profiles/{profile_id}", status_code=204)
def remove_profile(
    household_id: str, profile_id: str, actor: Identity,
    expected_version: Annotated[int, Query(ge=1)],
):
    delete_archive(actor[0], household_id, profile_id, expected_version)


@router.get("/households/{household_id}/inventory-lots")
def inventory(household_id: str, actor: Identity):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        return [
            {
                "id": row.id,
                "name": row.name,
                "quantity_status": row.quantity_status,
                "quantity": str(row.quantity) if row.quantity is not None else None,
                "unit": row.unit,
                "confirmed_at": row.confirmed_at.isoformat(),
            }
            for row in session.scalars(
                select(InventoryLot).where(InventoryLot.workspace_id == household_id).limit(100)
            )
        ]


@router.get("/households/{household_id}/procurement-items")
def procurement(household_id: str, actor: Identity):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        return [
            {
                "id": row.id,
                "name": row.name,
                "quantity_status": row.quantity_status,
                "status": row.status,
            }
            for row in session.scalars(
                select(ShoppingItem).where(ShoppingItem.workspace_id == household_id).limit(100)
            )
        ]


@router.get("/households/{household_id}/agent/sessions")
def sessions(
    household_id: str, actor: Identity,
    offset: int = Query(default=0, ge=0), limit: int = Query(default=30, ge=1, le=100),
):
    return turn_history.list_chats(actor[0], household_id, offset, limit)


@router.get("/households/{household_id}/agent/sessions/{session_id}")
def session_detail(
    household_id: str, session_id: str, actor: Identity,
    before: str | None = None, limit: int = Query(default=30, ge=1, le=100),
):
    return turn_history.chat_detail(actor[0], household_id, session_id, before, limit)


@router.delete("/households/{household_id}/agent/sessions/{session_id}")
def session_delete(household_id: str, session_id: str, actor: Identity):
    return turn_history.delete_chat(actor[0], household_id, session_id)


@router.post("/agent/turn-runs", status_code=202)
def turn(
    body: Turn,
    actor: Identity,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=120)],
):
    return runtime.create_turn(
        actor[0], body.household_id, body.session_id, body.text, body.parent_run_id, idempotency_key
    )


@router.get("/agent/turn-runs/{run_id}")
def run_status(run_id: str, actor: Identity):
    run = runtime.accessible_run(actor[0], run_id)
    return {
        "run_id": run.id,
        "session_id": run.session_id,
        "status": run.status,
        "result": run.result,
    }


@router.post("/agent/turn-runs/{run_id}/cancel", status_code=202)
def cancel(run_id: str, actor: Identity):
    runtime.cancel_run(actor[0], run_id)
    return {"status": "cancel_requested"}


@router.post("/actions/{action_id}/confirm")
def confirm(
    action_id: str,
    body: Decision,
    actor: Identity,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=120)],
):
    return runtime.decide_action(
        actor[0], action_id, True, idempotency_key, body.expected_version, body.payload_hash
    )


@router.post("/actions/{action_id}/reject")
def reject(
    action_id: str,
    body: Decision,
    actor: Identity,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=120)],
):
    return runtime.decide_action(
        actor[0], action_id, False, idempotency_key, body.expected_version, body.payload_hash
    )


@router.get("/agent/turn-runs/{run_id}/events")
def events(run_id: str, request: Request, actor: Identity):
    run = runtime.accessible_run(actor[0], run_id)
    if runtime.aware(run.events_expire_at) <= runtime.utc_now():
        raise HTTPException(410, detail={"code": "EVENT_HISTORY_EXPIRED"})
    cursor = request.headers.get("last-event-id", f"{run_id}:0")
    try:
        scope, seq = cursor.rsplit(":", 1)
        seq = int(seq)
        if scope != run_id or seq < 0 or seq > run.next_seq:
            raise ValueError
    except ValueError:  # Translate invalid SSE cursor syntax or range into an HTTP input error.
        raise HTTPException(400, detail={"code": "INVALID_EVENT_CURSOR"}) from None

    async def stream():
        nonlocal seq
        while not await request.is_disconnected():
            # A long-lived stream must not outlive the cookie session that opened it.
            try:
                authorization = request.headers.get("authorization", "")
                bearer = (
                    authorization[7:].strip()
                    if authorization.lower().startswith("bearer ")
                    else ""
                )
                current_user, _ = await asyncio.to_thread(
                    runtime.authenticate, bearer or request.cookies.get("mealflow_session", "")
                )
            except HTTPException:  # End the stream when its session is no longer authenticated.
                break
            if current_user != actor[0]:
                break
            current = await asyncio.to_thread(runtime.accessible_run, actor[0], run_id)
            with SessionLocal() as session:
                rows = session.scalars(
                    select(AgentEvent)
                    .where(AgentEvent.run_id == run_id, AgentEvent.seq > seq)
                    .order_by(AgentEvent.seq)
                    .limit(100)
                ).all()
            for row in rows:
                seq = row.seq
                body = {
                    "schema_version": "1.0",
                    "run_id": run_id,
                    "seq": seq,
                    "type": row.type,
                    "payload": row.payload,
                }
                data = json.dumps(body, ensure_ascii=False)
                yield f"id: {run_id}:{seq}\nevent: {row.type}\ndata: {data}\n\n"
            if current.status not in {"queued", "running"} and seq >= current.next_seq:
                break
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
