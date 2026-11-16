"""Authenticated API boundary for browser and native clients."""

import asyncio
import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, or_, select

from app.core.config import get_settings
from app.db.models import User
from app.db.session import SessionLocal
from app.db.v2_models import (
    AgentEvent,
    AuthSession,
    CookingLogPhoto,
    InventoryLot,
    ShoppingItem,
)
from app.services import cooking_logs as cooking_log_service
from app.services import fridge_observations, fridge_vision
from app.services import (
    ingredient_catalog,
    inventory_lots,
    offline_bundle,
    photo_store,
    recipe_actions,
    turn_history,
)
from app.services import v2_runtime as runtime
from app.services.food_constraints import household_constraints
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


class CookingDishInput(Input):
    recipe_id: str | None = Field(default=None, max_length=36)
    name: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=500)


class CookingLogCreate(Input):
    title: str = Field(min_length=1, max_length=120)
    cooked_at: datetime
    notes: str = Field(default="", max_length=2000)
    allow_assistant_citation: bool = False
    dishes: list[CookingDishInput] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def _timezone(self):
        if self.cooked_at.tzinfo is None:
            raise ValueError("cooked_at must include timezone")
        return self


class CookingLogPatch(Input):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    cooked_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)
    allow_assistant_citation: bool | None = None
    dishes: list[CookingDishInput] | None = Field(default=None, max_length=30)

    @model_validator(mode="after")
    def _timezone(self):
        if self.cooked_at is not None and self.cooked_at.tzinfo is None:
            raise ValueError("cooked_at must include timezone")
        return self


class CookingCommentInput(Input):
    body: str = Field(min_length=1, max_length=1000)


# Match Numeric(12, 3) before writing; excess fractional digits must never be rounded.
InventoryQuantity = Annotated[Decimal, Field(
    ge=0, le=Decimal("999999999.999"), max_digits=12, decimal_places=3,
)]


class InventoryPayload(Input):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    ingredient_id: str | None = Field(default=None, min_length=1, max_length=36)
    ingredient_key: str | None = Field(default=None, max_length=80)
    quantity_status: Literal["known", "unknown"] = "unknown"
    quantity: InventoryQuantity | None = None
    unit: str | None = Field(default=None, max_length=20)
    expires_on: date | None = None
    notes: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def validate_quantity(self):
        self.name = (self.name or "").strip() or None
        self.ingredient_id = (self.ingredient_id or "").strip() or None
        self.ingredient_key = (self.ingredient_key or "").strip() or None
        self.unit = (self.unit or "").strip() or None
        self.notes = (self.notes or "").strip()
        if not self.ingredient_id and not self.ingredient_key and not self.name:
            raise ValueError("ingredient_id, ingredient_key or name is required")
        if self.quantity_status == "unknown":
            self.quantity = None
            self.unit = None
        elif self.quantity is None or not self.unit:
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


class ObservationItemEdit(Input):
    id: str = Field(min_length=1, max_length=36)
    name: str = Field(min_length=1, max_length=40)
    kept: bool = True


class ObservationConfirm(Input):
    items: list[ObservationItemEdit] = Field(min_length=1, max_length=30)


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
    return inventory_lots.lot_view(row)


def _movement(session, actor_id: str, row: InventoryLot, kind: str, before: dict):
    inventory_lots.add_movement(session, actor_id, row, kind, before)


@router.get("/ingredients")
def ingredients(actor: Identity, search: str = Query(default=""),
                limit: int = Query(default=30, ge=1, le=100)):
    with SessionLocal() as session:
        # Auth gate only; catalog is shared and not household-scoped.
        _ = actor
        presets = ingredient_catalog.ensure_dictionary_presets(session)
        aliases = ingredient_catalog.ensure_preset_aliases(session)
        if presets or aliases:
            session.commit()
        rows = ingredient_catalog.search_ingredients(session, search, limit)
        return [ingredient_catalog.ingredient_view(row, session) for row in rows]


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
        try:
            ingredient = ingredient_catalog.resolve_ingredient(
                session,
                ingredient_id=body.ingredient_id,
                ingredient_key=body.ingredient_key,
                name=body.name,
                default_unit=body.unit,
            )
        except ingredient_catalog.IngredientNotFoundError as exc:
            raise HTTPException(
                400, detail={"code": "INGREDIENT_NOT_FOUND", "message": str(exc)}
            ) from exc
        view, outcome = inventory_lots.upsert_lot(
            session,
            workspace_id=household_id,
            actor_id=actor[0],
            ingredient=ingredient,
            name=body.name or ingredient.name,
            quantity_status=body.quantity_status,
            quantity=body.quantity,
            unit=body.unit,
            expires_on=body.expires_on,
            notes=body.notes,
        )
        session.commit()
        return {**view, "outcome": outcome}


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


class QuickActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["inventory", "restrictions", "skills"]
    query: str = Field(default="", max_length=200)
    offline: bool = False


@router.post("/households/{household_id}/assistant/quick-actions")
async def assistant_quick_action(
    household_id: str, body: QuickActionInput, actor: Identity
):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        result = recipe_actions.quick_action(
            session, household_id, body.action, body.query, offline=body.offline
        )
        fallback = (
            body.action == "restrictions"
            and not body.offline
            and result.fallback_allowed
        )
        constraints = household_constraints(session, household_id) if fallback else []
    if fallback:
        supplement = await recipe_actions.llm_recipe_action(
            constraints, body.query, version=result.data_version or ""
        )
        if supplement is not None:
            result = supplement
    payload = asdict(result)
    payload["household_id"] = household_id
    return payload


@router.get("/households/{household_id}/offline-bundle")
def household_offline_bundle(
    household_id: str, response: Response, actor: Identity
):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        bundle = offline_bundle.build_bundle(session, household_id)
    response.headers["ETag"] = f'"{bundle["data_version"]}"'
    response.headers["Cache-Control"] = "private, max-age=300"
    return bundle


@router.get("/households/{household_id}/recipes")
def household_recipes(
    household_id: str,
    actor: Identity,
    query: str = Query(default="", max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        return recipe_actions.browse_recipes(
            session, query=query, limit=limit, offset=offset
        )


@router.get("/households/{household_id}/recipes/{recipe_id}")
def household_recipe_detail(household_id: str, recipe_id: str, actor: Identity):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        recipe = recipe_actions.get_recipe(session, recipe_id)
    if recipe is None:
        raise HTTPException(404, detail={"code": "RECIPE_NOT_FOUND"})
    return recipe


def _cooking_error(exc: cooking_log_service.CookingLogError) -> None:
    detail: dict = {"code": exc.code}
    if exc.detail:
        detail["current_version"] = int(exc.detail)
    raise HTTPException(exc.status, detail=detail)


@router.get("/households/{household_id}/cooking-logs")
def cooking_log_list(
    household_id: str,
    actor: Identity,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    mine: bool = Query(default=False),
):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        return {
            "items": cooking_log_service.list_logs(
                session,
                household_id,
                actor[0],
                limit=limit,
                offset=offset,
                creator_id=actor[0] if mine else None,
            )
        }


@router.post("/households/{household_id}/cooking-logs", status_code=201)
def cooking_log_create(household_id: str, body: CookingLogCreate, actor: Identity):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        try:
            return cooking_log_service.create_log(
                session,
                workspace_id=household_id,
                creator_id=actor[0],
                title=body.title,
                cooked_at=body.cooked_at,
                notes=body.notes,
                allow_assistant_citation=body.allow_assistant_citation,
                dishes=[item.model_dump() for item in body.dishes],
            )
        except cooking_log_service.CookingLogError as exc:
            _cooking_error(exc)


@router.get("/households/{household_id}/cooking-logs/{log_id}")
def cooking_log_detail(household_id: str, log_id: str, actor: Identity):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        try:
            return cooking_log_service.get_log(session, household_id, log_id, actor[0])
        except cooking_log_service.CookingLogError as exc:
            _cooking_error(exc)


@router.patch("/households/{household_id}/cooking-logs/{log_id}")
def cooking_log_update(
    household_id: str, log_id: str, body: CookingLogPatch, actor: Identity
):
    changes = body.model_dump(exclude_unset=True)
    expected_version = changes.pop("expected_version")
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        try:
            return cooking_log_service.update_log(
                session,
                workspace_id=household_id,
                log_id=log_id,
                editor_id=actor[0],
                expected_version=expected_version,
                **changes,
            )
        except cooking_log_service.CookingLogError as exc:
            _cooking_error(exc)


@router.post("/households/{household_id}/cooking-logs/{log_id}/comments", status_code=201)
def cooking_log_comment(
    household_id: str, log_id: str, body: CookingCommentInput, actor: Identity
):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        try:
            return cooking_log_service.add_comment(
                session,
                workspace_id=household_id,
                log_id=log_id,
                author_id=actor[0],
                body=body.body,
            )
        except cooking_log_service.CookingLogError as exc:
            _cooking_error(exc)


@router.post("/households/{household_id}/cooking-logs/{log_id}/photos", status_code=201)
async def cooking_log_photo_upload(
    household_id: str, log_id: str, request: Request, actor: Identity
):
    data = await request.body()
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        try:
            return cooking_log_service.add_photo(
                session,
                workspace_id=household_id,
                log_id=log_id,
                editor_id=actor[0],
                data=data,
            )
        except cooking_log_service.CookingLogError as exc:
            _cooking_error(exc)
        except photo_store.PhotoRejected as exc:
            raise HTTPException(
                400, detail={"code": "PHOTO_REJECTED", "message": str(exc)}
            ) from exc


@router.delete("/households/{household_id}/cooking-logs/{log_id}/photos/{photo_id}")
def cooking_log_photo_delete(
    household_id: str, log_id: str, photo_id: str, actor: Identity
):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        try:
            return cooking_log_service.delete_photo(
                session,
                workspace_id=household_id,
                log_id=log_id,
                photo_id=photo_id,
                editor_id=actor[0],
            )
        except cooking_log_service.CookingLogError as exc:
            _cooking_error(exc)


@router.get("/households/{household_id}/cooking-logs/{log_id}/photos/{photo_id}")
def cooking_log_photo(
    household_id: str, log_id: str, photo_id: str, actor: Identity
):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        try:
            cooking_log_service.active_log(session, household_id, log_id)
        except cooking_log_service.CookingLogError as exc:
            _cooking_error(exc)
        photo = session.get(CookingLogPhoto, photo_id)
        if photo is None or photo.log_id != log_id:
            raise HTTPException(404, detail={"code": "COOKING_LOG_PHOTO_NOT_FOUND"})
        try:
            path = photo_store.path_for(photo.storage_key)
        except photo_store.PhotoRejected as exc:
            raise HTTPException(
                404, detail={"code": "COOKING_LOG_PHOTO_NOT_FOUND"}
            ) from exc
        if not path.is_file():
            raise HTTPException(404, detail={"code": "COOKING_LOG_PHOTO_NOT_FOUND"})
        return FileResponse(
            path,
            media_type=photo.content_type,
            headers={
                "Cache-Control": "private, max-age=3600",
                "X-Content-Type-Options": "nosniff",
            },
        )


def _observation_http(exc: fridge_observations.ObservationError) -> None:
    status = {
        "OBSERVATION_NOT_FOUND": 404,
        "OBSERVATION_CLOSED": 409,
        "OBSERVATION_EMPTY": 422,
        "INVALID_NAME": 422,
    }.get(exc.code, 400)
    raise HTTPException(status, detail={"code": exc.code, "message": exc.message})


def _vision_http(exc: fridge_vision.VisionError) -> None:
    status = 503 if isinstance(exc, fridge_vision.VisionUnavailable) else 502
    raise HTTPException(status, detail={"code": exc.code, "message": exc.message})


@router.post("/households/{household_id}/fridge-observations", status_code=201)
async def create_fridge_observation(household_id: str, request: Request, actor: Identity):
    data = await request.body()
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
    try:
        stored = photo_store.store(data, prefix="fridge")
        clean = photo_store.path_for(stored["storage_key"]).read_bytes()
        detected = fridge_vision.segment_image(clean, stored["content_type"])
    except photo_store.PhotoRejected as exc:
        raise HTTPException(400, detail={"code": "PHOTO_REJECTED", "message": str(exc)}) from exc
    except fridge_vision.VisionError as exc:
        if "stored" in locals() and stored.get("created"):
            photo_store.remove(stored["storage_key"])
        _vision_http(exc)
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        try:
            return fridge_observations.save_observation(
                session,
                workspace_id=household_id,
                actor_id=actor[0],
                stored=stored,
                detected=detected,
            )
        except Exception:
            if stored.get("created"):
                fridge_observations.drop_if_unreferenced(session, stored["storage_key"])
            raise


@router.get("/households/{household_id}/fridge-observations")
def list_fridge_observations(
    household_id: str,
    actor: Identity,
    status: Literal["pending", "confirmed", "discarded"] = "pending",
):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        return {"items": fridge_observations.list_observations(
            session, household_id, status=status
        )}


@router.get("/households/{household_id}/fridge-observations/{observation_id}")
def fridge_observation_detail(household_id: str, observation_id: str, actor: Identity):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        try:
            return fridge_observations.view(
                fridge_observations.get_observation(session, household_id, observation_id)
            )
        except fridge_observations.ObservationError as exc:
            _observation_http(exc)


@router.get("/households/{household_id}/fridge-observations/{observation_id}/image")
def fridge_observation_image(household_id: str, observation_id: str, actor: Identity):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        try:
            row = fridge_observations.get_observation(session, household_id, observation_id)
        except fridge_observations.ObservationError as exc:
            _observation_http(exc)
        try:
            path = photo_store.path_for(row.storage_key)
        except photo_store.PhotoRejected as exc:
            raise HTTPException(404, detail={"code": "OBSERVATION_NOT_FOUND"}) from exc
        if not path.is_file():
            raise HTTPException(404, detail={"code": "OBSERVATION_NOT_FOUND"})
        return FileResponse(
            path,
            media_type=row.content_type,
            headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
        )


@router.post("/households/{household_id}/fridge-observations/{observation_id}/confirm")
def confirm_fridge_observation(
    household_id: str, observation_id: str, body: ObservationConfirm, actor: Identity
):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        try:
            return fridge_observations.confirm_observation(
                session,
                workspace_id=household_id,
                actor_id=actor[0],
                observation_id=observation_id,
                edits=[item.model_dump() for item in body.items],
            )
        except fridge_observations.ObservationError as exc:
            _observation_http(exc)


@router.post("/households/{household_id}/fridge-observations/{observation_id}/discard")
def discard_fridge_observation(household_id: str, observation_id: str, actor: Identity):
    with SessionLocal() as session:
        runtime.authorized_workspace(session, actor[0], household_id)
        try:
            return fridge_observations.discard_observation(session, household_id, observation_id)
        except fridge_observations.ObservationError as exc:
            _observation_http(exc)
