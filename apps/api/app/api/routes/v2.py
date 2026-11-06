"""Authenticated API boundary for browser and native clients."""

import asyncio
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, or_, select

from app.core.config import get_settings
from app.db.models import User
from app.db.session import SessionLocal
from app.db.v2_models import AgentEvent, AuthSession, InventoryLot, InventoryMovement, ShoppingItem
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


# Match Numeric(12, 3) before writing; excess fractional digits must never be rounded.
InventoryQuantity = Annotated[Decimal, Field(
    ge=0, le=Decimal("999999999.999"), max_digits=12, decimal_places=3,
)]


class InventoryPayload(Input):
    name: str = Field(min_length=1, max_length=120)
    ingredient_key: str | None = Field(default=None, max_length=80)
    quantity_status: Literal["known", "unknown"] = "unknown"
    quantity: InventoryQuantity | None = None
    unit: str | None = Field(default=None, max_length=20)
    expires_on: date | None = None
    notes: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def validate_quantity(self):
        if self.quantity_status == "unknown":
            self.quantity = None
            self.unit = None
        elif self.quantity is None or not self.unit or not self.unit.strip():
            raise ValueError("known quantity requires quantity and unit")
        return self


class InventoryPatch(Input):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    quantity_status: Literal["known", "unknown"] | None = None
    quantity: InventoryQuantity | None = None
    unit: str | None = Field(default=None, max_length=20)
    expires_on: date | None = None
    notes: str | None = Field(default=None, max_length=300)
    expected_version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_quantity(self):
        fields = self.model_fields_set
        if any(field in fields and getattr(self, field) is None
               for field in ("name", "quantity_status", "notes")):
            raise ValueError("name, quantity_status and notes cannot be null")
        if self.quantity_status == "unknown":
            self.quantity = None
            self.unit = None
        if self.quantity_status == "known" and (
            self.quantity is None or not self.unit or not self.unit.strip()
        ):
            raise ValueError("known quantity requires quantity and unit")
        return self


class InventoryTransition(Input):
    expected_version: int = Field(ge=1)


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
    with SessionLocal() as session:
        phone = session.get(User, actor[0]).phone
    return {"user_id": actor[0], "phone_masked": f"{phone[:3]}****{phone[-4:]}" if phone else ""}


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
    return {"id": runtime.create_single_household(actor[0], body.name)}


@router.post("/households/personal/ensure")
def ensure_personal_household(actor: Identity):
    return {"id": runtime.ensure_personal_household(actor[0])}


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


def _lot_view(row: InventoryLot) -> dict:
    return {"id": row.id, "name": row.name, "ingredient_key": row.ingredient_key,
            "quantity_status": row.quantity_status,
            "quantity": str(row.quantity) if row.quantity is not None else None,
            "unit": row.unit, "expires_on": row.expires_on.isoformat() if row.expires_on else None,
            "notes": row.notes, "status": row.status,
            "confirmed_at": row.confirmed_at.isoformat(), "version": row.version}


def _movement(session, actor_id: str, row: InventoryLot, kind: str, before: dict):
    session.add(InventoryMovement(workspace_id=row.workspace_id, lot_id=row.id,
        actor_id=actor_id, kind=kind, before=before, after=_lot_view(row),
        source_type="mobile", source_id=uuid4().hex))


@router.get("/households/{household_id}/inventory-lots")
def inventory(household_id: str, actor: Identity,
              offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100),
              search: str = Query(default=""),
              quantity_status: Literal["known", "unknown"] | None = None,
              status: Literal["active", "consumed", "removed", "all"] = "active"):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        query = select(InventoryLot).where(InventoryLot.workspace_id == household_id)
        if status != "all":
            query = query.where(InventoryLot.status == status)
        if search.strip():
            term = f"%{search.strip()}%"
            query = query.where(or_(
                InventoryLot.name.ilike(term), InventoryLot.ingredient_key.ilike(term)
            ))
        if quantity_status:
            query = query.where(InventoryLot.quantity_status == quantity_status)
        total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
        items = [_lot_view(row) for row in session.scalars(
            query.order_by(InventoryLot.name, InventoryLot.id).offset(offset).limit(limit)
        )]
        return {"items": items, "total": total, "offset": offset, "limit": limit,
                "has_more": offset + len(items) < total}


@router.get("/households/{household_id}/inventory-summary")
def inventory_summary(household_id: str, actor: Identity):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        rows = session.execute(select(InventoryLot.quantity_status, func.count()).where(
            InventoryLot.workspace_id == household_id, InventoryLot.status == "active"
        ).group_by(InventoryLot.quantity_status)).all()
        counts = {status: count for status, count in rows}
        return {"total": sum(counts.values()), "known": counts.get("known", 0),
                "unknown": counts.get("unknown", 0)}


@router.get("/households/{household_id}/inventory-lots/{lot_id}")
def inventory_detail(household_id: str, lot_id: str, actor: Identity):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        row = session.scalar(select(InventoryLot).where(
            InventoryLot.id == lot_id, InventoryLot.workspace_id == household_id
        ))
        if row is None:
            raise HTTPException(404, detail={"code": "INVENTORY_NOT_FOUND"})
        return _lot_view(row)


@router.post("/households/{household_id}/inventory-lots", status_code=201)
def create_inventory(household_id: str, body: InventoryPayload, actor: Identity):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        row = InventoryLot(
            # Preserve existing short keys; long display names get a stable bounded key.
            workspace_id=household_id, ingredient_key=body.ingredient_key or (
                body.name if len(body.name) <= 80 else sha256(body.name.encode()).hexdigest()
            ),
            name=body.name, quantity_status=body.quantity_status, quantity=body.quantity,
            unit=body.unit, expires_on=body.expires_on, notes=body.notes,
            confirmed_at=datetime.now(UTC), source_type="mobile",
            source_id=uuid4().hex, version=1)
        session.add(row)
        session.flush()
        _movement(session, actor[0], row, "added", {})
        session.commit()
        return _lot_view(row)


@router.patch("/households/{household_id}/inventory-lots/{lot_id}")
def update_inventory(household_id: str, lot_id: str, body: InventoryPatch, actor: Identity):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        # Hold the row lock through the movement insert and commit so stale writers see 409.
        row = session.scalar(select(InventoryLot).where(
            InventoryLot.id == lot_id, InventoryLot.workspace_id == household_id
        ).with_for_update())
        if row is None:
            raise HTTPException(404, detail={"code": "INVENTORY_NOT_FOUND"})
        if row.version != body.expected_version:
            raise HTTPException(409, detail={"code": "INVENTORY_VERSION_CONFLICT"})
        before = _lot_view(row)
        fields = body.model_fields_set
        next_status = body.quantity_status or row.quantity_status
        next_quantity = body.quantity if "quantity" in fields else row.quantity
        next_unit = body.unit if "unit" in fields else row.unit
        if next_status == "known" and (
            next_quantity is None or not next_unit or not next_unit.strip()
        ):
            raise HTTPException(422, detail={"code": "KNOWN_QUANTITY_REQUIRED"})
        for field in ("name", "quantity_status", "quantity", "unit", "expires_on", "notes"):
            if field in fields:
                setattr(row, field, getattr(body, field))
        if row.quantity_status == "unknown":
            row.quantity = None
            row.unit = None
        row.version += 1
        row.confirmed_at = datetime.now(UTC)
        _movement(session, actor[0], row, "updated", before)
        session.commit()
        return _lot_view(row)


@router.post("/households/{household_id}/inventory-lots/{lot_id}/consume")
def consume_inventory(household_id: str, lot_id: str, body: InventoryTransition, actor: Identity):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        row = session.scalar(select(InventoryLot).where(
            InventoryLot.id == lot_id, InventoryLot.workspace_id == household_id
        ).with_for_update())
        if row is None:
            raise HTTPException(404, detail={"code": "INVENTORY_NOT_FOUND"})
        if row.version != body.expected_version:
            raise HTTPException(409, detail={"code": "INVENTORY_VERSION_CONFLICT"})
        if row.status != "active":
            raise HTTPException(409, detail={"code": "INVENTORY_STATE_CONFLICT"})
        before = _lot_view(row)
        # Terminal transitions are single-shot; never create movements after leaving active.
        row.status = "consumed"
        row.version += 1
        _movement(session, actor[0], row, "consumed", before)
        session.commit()
        return _lot_view(row)


@router.delete("/households/{household_id}/inventory-lots/{lot_id}", status_code=204)
def remove_inventory(household_id: str, lot_id: str, body: InventoryTransition, actor: Identity):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        row = session.scalar(select(InventoryLot).where(
            InventoryLot.id == lot_id, InventoryLot.workspace_id == household_id
        ).with_for_update())
        if row is None:
            raise HTTPException(404, detail={"code": "INVENTORY_NOT_FOUND"})
        if row.version != body.expected_version:
            raise HTTPException(409, detail={"code": "INVENTORY_VERSION_CONFLICT"})
        if row.status != "active":
            raise HTTPException(409, detail={"code": "INVENTORY_STATE_CONFLICT"})
        before = _lot_view(row)
        # Terminal transitions are single-shot; never create movements after leaving active.
        row.status = "removed"
        row.version += 1
        _movement(session, actor[0], row, "removed", before)
        session.commit()


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
