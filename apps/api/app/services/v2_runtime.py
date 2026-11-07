from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import User, Workspace, WorkspaceMember, new_id
from app.db.session import SessionLocal
from app.db.v2_models import (
    AgentEvent,
    AuditEvent,
    AuthSession,
    BackgroundJob,
    HouseholdInvite,
    HouseholdProfile,
    IdempotencyRecord,
    InventoryLot,
    LoginAttempt,
    PendingAction,
    RuntimeAdmission,
    ShoppingItem,
    TurnRun,
    TurnSession,
)
from app.services import ingredient_catalog, inventory_lots


def utc_now() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime) -> datetime:
    # Normalize timestamps supplied by callers as well as database values.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return digest(raw)


def authenticate(token: str) -> tuple[str, str]:
    now = utc_now()
    with SessionLocal() as session:
        record = session.scalar(select(AuthSession).where(AuthSession.token_hash == digest(token)))
        if (
            record is None
            or record.revoked
            or aware(record.expires_at) <= now
            or aware(record.idle_until) <= now
        ):
            raise HTTPException(401, detail={"code": "UNAUTHENTICATED"})
        record.idle_until = min(aware(record.expires_at), now + timedelta(hours=24))
        session.commit()
        return record.user_id, record.id


def authorized_workspace(session: Session, user_id: str, workspace_id: str) -> WorkspaceMember:
    membership = session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.user_id == user_id,
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.status == "active",
        )
    )
    if membership is None:
        raise HTTPException(404, detail={"code": "HOUSEHOLD_NOT_FOUND"})
    return membership


def login(phone: str, password: str, *, native_client: bool = False) -> tuple[str, str, str]:
    now = utc_now()
    with SessionLocal() as session:
        key = digest(phone)
        attempt = session.get(LoginAttempt, key, with_for_update=True)
        if attempt and aware(attempt.window_until) > now and attempt.failures >= 5:
            raise HTTPException(429, detail={"code": "LOGIN_RATE_LIMITED"})
        user = session.scalar(select(User).where(User.phone == phone))
        valid = False
        if user:
            from argon2 import PasswordHasher
            from argon2.exceptions import InvalidHashError, VerificationError

            try:
                PasswordHasher().verify(user.password_hash, password)
                valid = True
            except (VerificationError, InvalidHashError):  # Record a failed login attempt.
                pass
        if not valid:
            if attempt is None:
                attempt = LoginAttempt(
                    key=key, failures=0, window_until=now + timedelta(minutes=15)
                )
                session.add(attempt)
            if aware(attempt.window_until) <= now:
                attempt.failures, attempt.window_until = 0, now + timedelta(minutes=15)
            attempt.failures += 1
            session.commit()
            raise HTTPException(401, detail={"code": "INVALID_CREDENTIALS"})
        if attempt:
            session.delete(attempt)
        raw, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        session.add(
            AuthSession(
                user_id=user.id,
                token_hash=digest(raw),
                csrf_hash=digest(csrf),
                native_client=native_client,
                expires_at=now + timedelta(days=7),
                idle_until=now + timedelta(hours=24),
                revoked=False,
            )
        )
        session.commit()
        return raw, csrf, user.id


def household_list(user_id: str) -> list[dict[str, str]]:
    with SessionLocal() as session:
        rows = session.execute(
            select(Workspace)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == user_id, WorkspaceMember.status == "active")
        ).scalars().all()
        return [
            {"id": workspace.id, "name": workspace.name, "role": "user"} for workspace in rows
        ]


def create_household(user_id: str, name: str) -> str:
    with SessionLocal() as session, session.begin():
        return _insert_household(session, user_id, name)


def _insert_household(session: Session, user_id: str, name: str) -> str:
    household = Workspace(id=new_id(), name=name, kind="personal")
    session.add(household)
    session.flush()
    session.add(WorkspaceMember(
        workspace_id=household.id, user_id=user_id, role="user", status="active"
    ))
    session.add(HouseholdProfile(
        workspace_id=household.id, display_name="我", linked_user_id=user_id
    ))
    return household.id


def ensure_personal_household(user_id: str) -> str:
    with SessionLocal() as session, session.begin():
        # Serialise first-login provisioning so two devices cannot create two spaces.
        session.get(User, user_id, with_for_update=True)
        existing = session.scalar(select(WorkspaceMember.workspace_id).where(
            WorkspaceMember.user_id == user_id, WorkspaceMember.status == "active"
        ).order_by(WorkspaceMember.created_at))
        return existing or _insert_household(session, user_id, "我的家庭")


def create_single_household(user_id: str, name: str) -> str:
    with SessionLocal() as session, session.begin():
        session.get(User, user_id, with_for_update=True)
        if session.scalar(select(WorkspaceMember.workspace_id).where(
            WorkspaceMember.user_id == user_id, WorkspaceMember.status == "active"
        )):
            raise HTTPException(409, detail={"code": "HOUSEHOLD_LIMIT"})
        return _insert_household(session, user_id, name)


def create_invite(user_id: str, workspace_id: str) -> str:
    token = secrets.token_urlsafe(32)
    with SessionLocal() as session, session.begin():
        authorized_workspace(session, user_id, workspace_id)
        session.add(
            HouseholdInvite(
                workspace_id=workspace_id,
                token_hash=digest(token),
                expires_at=utc_now() + timedelta(hours=24),
            )
        )
    return token


def accept_invite(user_id: str, token: str) -> str:
    with SessionLocal() as session, session.begin():
        invite = session.scalar(
            select(HouseholdInvite)
            .where(HouseholdInvite.token_hash == digest(token), HouseholdInvite.used_at.is_(None))
            .with_for_update()
        )
        if invite is None or aware(invite.expires_at) <= utc_now():
            raise HTTPException(410, detail={"code": "INVITE_EXPIRED"})
        # A user may share one household, but cannot join a second one.
        session.get(User, user_id, with_for_update=True)
        active = session.scalar(select(WorkspaceMember.workspace_id).where(
            WorkspaceMember.user_id == user_id, WorkspaceMember.status == "active"
        ))
        if active and active != invite.workspace_id:
            raise HTTPException(409, detail={"code": "HOUSEHOLD_LIMIT"})
        # Different invitations accepted concurrently must not race the member unique key.
        session.get(Workspace, invite.workspace_id, with_for_update=True)
        invite.used_at = utc_now()
        member = session.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == invite.workspace_id,
                WorkspaceMember.user_id == user_id,
            )
        )
        if member:
            member.status = "active"
        else:
            session.add(
                WorkspaceMember(
                    workspace_id=invite.workspace_id,
                    user_id=user_id,
                    role="user",
                    status="active",
                )
            )
        return invite.workspace_id


def create_profile(user_id: str, workspace_id: str, name: str, linked_user_id: str | None) -> dict:
    with SessionLocal() as session, session.begin():
        authorized_workspace(session, user_id, workspace_id)
        if linked_user_id:
            member = session.scalar(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == linked_user_id,
                    WorkspaceMember.status == "active",
                )
            )
            if member is None:
                raise HTTPException(422, detail={"code": "PROFILE_USER_NOT_MEMBER"})
        profile = HouseholdProfile(
            workspace_id=workspace_id,
            display_name=name,
            linked_user_id=linked_user_id,
            restrictions=[],
            preferences=[],
        )
        session.add(profile)
        session.flush()
        return {"id": profile.id, "display_name": name, "linked_user_id": linked_user_id}


def list_profiles(user_id: str, workspace_id: str) -> list[dict]:
    with SessionLocal() as session:
        authorized_workspace(session, user_id, workspace_id)
        return [
            {
                "id": row.id,
                "display_name": row.display_name,
                "linked_user_id": row.linked_user_id,
                "restrictions": row.restrictions,
                "preferences": row.preferences,
                "legacy_details": row.legacy_details,
                "version": row.version,
            }
            for row in session.scalars(
                select(HouseholdProfile).where(HouseholdProfile.workspace_id == workspace_id)
            ).all()
        ]


def reserve_idempotency(
    session: Session, workspace_id: str, actor_id: str, operation: str, key: str,
    request_hash: str,
) -> tuple[IdempotencyRecord, bool]:
    """Serialize a key before side effects; a losing insert must not abort PG's transaction."""
    query = select(IdempotencyRecord).where(
        IdempotencyRecord.workspace_id == workspace_id,
        IdempotencyRecord.actor_id == actor_id,
        IdempotencyRecord.operation == operation,
        IdempotencyRecord.key == key,
    )
    previous = session.scalar(query)
    if previous is None:
        record = IdempotencyRecord(
            workspace_id=workspace_id, actor_id=actor_id, operation=operation,
            key=key, request_hash=request_hash, result={},
        )
        try:
            with session.begin_nested():
                session.add(record)
                session.flush()
            return record, False
        except IntegrityError:
            # READ COMMITTED sees the committed winner after the unique insert waits.
            # Only the savepoint was rolled back, so the outer transaction is usable.
            previous = session.scalar(query.execution_options(populate_existing=True))
            if previous is None:
                raise
    if previous.request_hash != request_hash:
        raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
    return previous, True


def create_turn(
    user_id: str,
    workspace_id: str,
    session_id: str | None,
    text: str,
    parent_run_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    now = utc_now()
    with SessionLocal() as session, session.begin():
        authorized_workspace(session, user_id, workspace_id)
        request_hash = canonical_hash(
            {"session_id": session_id, "text": text, "parent": parent_run_id}
        )
        record = None
        if idempotency_key:
            record, replay = reserve_idempotency(
                session, workspace_id, user_id, "create_turn", idempotency_key, request_hash
            )
            if replay:
                return record.result
        # Lock the conversation before checking its active run (PG concurrent requests).
        chat = session.get(TurnSession, session_id, with_for_update=True) if session_id else None
        if session_id and chat is None:
            raise HTTPException(404, detail={"code": "SESSION_NOT_FOUND"})
        if chat and (
            chat.workspace_id != workspace_id or chat.created_by != user_id
            or chat.state.get("deleted_at")
        ):
            raise HTTPException(404, detail={"code": "SESSION_NOT_FOUND"})
        if chat is None:
            chat = TurnSession(workspace_id=workspace_id, created_by=user_id, state={})
            session.add(chat)
            session.flush()
        if chat.active_run_id:
            active = session.get(TurnRun, chat.active_run_id)
            if active and active.status in {"queued", "running"}:
                raise HTTPException(409, detail={"code": "RUN_BUSY", "run_id": active.id})
        if parent_run_id:
            parent = session.get(TurnRun, parent_run_id)
            if (
                parent is None
                or parent.session_id != chat.id
                or parent.workspace_id != workspace_id
            ):
                raise HTTPException(404, detail={"code": "PARENT_RUN_NOT_FOUND"})
        # A seeded singleton serializes admission across API processes and households.
        # PostgreSQL holds the row lock until commit.
        gate = session.execute(
            update(RuntimeAdmission).where(RuntimeAdmission.id == "agent_turn")
            .values(id="agent_turn")
        )
        if gate.rowcount != 1:
            raise HTTPException(503, detail={"code": "ADMISSION_NOT_INITIALIZED"})
        settings = get_settings()
        active_count = session.scalar(
            select(func.count()).select_from(TurnRun).where(
                TurnRun.workspace_id == workspace_id,
                TurnRun.status.in_(["queued", "running"]),
            )
        )
        if active_count >= settings.runtime_household_active_limit:
            raise HTTPException(429, detail={"code": "HOUSEHOLD_RUN_LIMIT"})
        queued_count = session.scalar(
            select(func.count()).select_from(BackgroundJob).where(
                BackgroundJob.kind == "agent_turn", BackgroundJob.status == "queued",
            )
        )
        if queued_count >= settings.runtime_queue_capacity:
            raise HTTPException(503, detail={"code": "QUEUE_FULL"})
        run = TurnRun(
            workspace_id=workspace_id,
            actor_id=user_id,
            session_id=chat.id,
            parent_run_id=parent_run_id,
            request={"text": text},
            state=(
                {"previous_action_result": chat.state["previous_action_result"]}
                if chat.state.get("previous_action_result") is not None
                else {}
            ),
            result={},
            status="queued",
            cancel_requested=False,
            next_seq=0,
            events_expire_at=now + timedelta(days=7),
        )
        session.add(run)
        session.flush()
        chat.active_run_id = run.id
        session.add(
            AgentEvent(
                run_id=run.id, seq=1, type="status", payload={"status": "queued"}, created_at=now
            )
        )
        run.next_seq = 1
        session.add(
            BackgroundJob(
                kind="agent_turn",
                business_id=run.id,
                dedupe_key=f"turn:{run.id}",
                status="queued",
                attempts=0,
                next_run_at=now,
            )
        )
        result = {"run_id": run.id, "session_id": chat.id, "status": run.status}
        if record is not None:
            record.result = result
        return result


def append_event(session: Session, run: TurnRun, kind: str, payload: dict) -> None:
    run.next_seq += 1
    session.add(
        AgentEvent(
            run_id=run.id, seq=run.next_seq, type=kind, payload=payload, created_at=utc_now()
        )
    )


def prepare_action(user_id: str, workspace_id: str, kind: str, payload: dict) -> dict:
    validate_action(kind, payload)
    with SessionLocal() as session, session.begin():
        authorized_workspace(session, user_id, workspace_id)
        row = PendingAction(
            workspace_id=workspace_id,
            actor_id=user_id,
            run_id=None,
            type=kind,
            payload=payload,
            payload_hash=canonical_hash(payload),
            version=1,
            status="pending",
            expires_at=utc_now() + timedelta(hours=24),
            result={},
        )
        session.add(row)
        session.flush()
        return {
            "id": row.id,
            "type": kind,
            "payload": payload,
            "payload_hash": row.payload_hash,
            "version": row.version,
            "expires_at": row.expires_at.isoformat(),
        }


def validate_action(kind: str, payload: dict) -> None:
    if kind not in {"add_procurement", "add_inventory", "save_preference"}:
        raise HTTPException(422, detail={"code": "UNSUPPORTED_ACTION"})
    if kind == "save_preference":
        if (
            not payload.get("profile_id")
            or not payload.get("preference")
            or not isinstance(payload.get("expected_version"), int)
        ):
            raise HTTPException(422, detail={"code": "INVALID_ACTION_PAYLOAD"})
        return
    if not isinstance(payload.get("name"), str) or not payload["name"].strip():
        raise HTTPException(422, detail={"code": "INVALID_ACTION_PAYLOAD"})
    status = payload.get("quantity_status", "unknown")
    quantity, unit = payload.get("quantity"), payload.get("unit")
    from decimal import Decimal, InvalidOperation

    try:
        valid = (status == "unknown" and quantity is None and unit is None) or (
            status == "known"
            and quantity is not None
            and Decimal(str(quantity)).is_finite()
            and Decimal(str(quantity)) >= 0
            and isinstance(unit, str)
            and bool(unit.strip())
        )
    except InvalidOperation:  # Report invalid decimal input as a quantity validation error.
        valid = False
    if not valid or (kind == "add_inventory" and not payload.get("ingredient_key")):
        raise HTTPException(422, detail={"code": "INVALID_QUANTITY"})


def decide_action(
    user_id: str,
    action_id: str,
    accept: bool,
    idempotency_key: str,
    expected_version: int | None = None,
    payload_hash: str | None = None,
) -> dict:
    with SessionLocal() as session, session.begin():
        action = session.get(PendingAction, action_id)
        if action is None or action.actor_id != user_id:
            raise HTTPException(404, detail={"code": "ACTION_NOT_FOUND"})
        authorized_workspace(session, user_id, action.workspace_id)
        if (expected_version is not None and expected_version != action.version) or (
            payload_hash is not None and payload_hash != action.payload_hash
        ):
            raise HTTPException(409, detail={"code": "ACTION_STALE"})
        op = "confirm" if accept else "reject"
        request_hash = canonical_hash(
            {
                "action": action_id,
                "accept": accept,
                "payload_hash": action.payload_hash,
                "version": action.version,
            }
        )
        record, replay = reserve_idempotency(
            session, action.workspace_id, user_id, op, idempotency_key, request_hash
        )
        if replay:
            return record.result
        # Acquire the business lock only after the key lock, then discard the stale read.
        action = session.get(
            PendingAction, action_id, with_for_update=True, populate_existing=True
        )
        if action.status != "pending":
            if action.result and action.status == ("confirmed" if accept else "rejected"):
                record.result = action.result
                return action.result
            raise HTTPException(409, detail={"code": "ACTION_ALREADY_DECIDED"})
        if aware(action.expires_at) <= utc_now():
            raise HTTPException(409, detail={"code": "ACTION_EXPIRED"})
        if action.payload_hash != canonical_hash(action.payload):
            raise HTTPException(409, detail={"code": "ACTION_STALE"})
        validate_action(action.type, action.payload)
        result: dict[str, Any] = {"action_id": action.id, "status": "rejected"}
        if accept and action.type == "add_procurement":
            body = action.payload
            item = ShoppingItem(
                workspace_id=action.workspace_id,
                name=body["name"],
                ingredient_key=body.get("ingredient_key", "unknown"),
                quantity_status=body.get("quantity_status", "unknown"),
                quantity=body.get("quantity"),
                unit=body.get("unit"),
                note=body.get("note", ""),
                status="pending",
                sources=[],
            )
            session.add(item)
            session.flush()
            result = {"action_id": action.id, "status": "confirmed", "shopping_item_id": item.id}
        elif accept and action.type == "add_inventory":
            body = action.payload
            existing = session.scalar(
                select(InventoryLot).where(
                    InventoryLot.workspace_id == action.workspace_id,
                    InventoryLot.source_type == "pending_action",
                    InventoryLot.source_id == action.id,
                )
            )
            if existing is None:
                ingredient = ingredient_catalog.resolve_ingredient(
                    session,
                    ingredient_key=body.get("ingredient_key"),
                    name=body.get("name"),
                    default_unit=body.get("unit"),
                )
                view, _outcome = inventory_lots.upsert_lot(
                    session,
                    workspace_id=action.workspace_id,
                    actor_id=user_id,
                    ingredient=ingredient,
                    name=body.get("name") or ingredient.name,
                    quantity_status=body.get("quantity_status", "unknown"),
                    quantity=body.get("quantity"),
                    unit=body.get("unit"),
                    expires_on=(
                        date.fromisoformat(body["expires_on"])
                        if body.get("expires_on")
                        else None
                    ),
                    notes=body.get("notes", ""),
                )
                # Preserve pending_action idempotency markers on the resulting lot.
                lot = session.get(InventoryLot, view["id"])
                lot.source_type = "pending_action"
                lot.source_id = action.id
                result = {
                    "action_id": action.id,
                    "status": "confirmed",
                    "inventory_lot_id": lot.id,
                }
            else:
                result = {
                    "action_id": action.id,
                    "status": "confirmed",
                    "inventory_lot_id": existing.id,
                }
        elif accept and action.type == "save_preference":
            body = action.payload
            profile = session.get(HouseholdProfile, body["profile_id"], with_for_update=True)
            if (
                profile is None
                or profile.workspace_id != action.workspace_id
                or profile.version != body["expected_version"]
            ):
                raise HTTPException(409, detail={"code": "ACTION_STALE"})
            profile.preferences = [*profile.preferences, body["preference"]]
            profile.version += 1
            result = {"action_id": action.id, "status": "confirmed", "profile_id": profile.id}
        action.status = "confirmed" if accept else "rejected"
        action.result = result
        record.result = result

        # The worker has finished this run already. Resolve its preview in the
        # same transaction as the action so restore and SSE cannot expose the
        # stale waiting_confirmation card. The action lock is acquired first;
        # the worker uses job -> run -> chat, so this cannot invert its locks.
        if action.run_id:
            run = session.get(TurnRun, action.run_id, with_for_update=True)
            if (
                run is not None
                and run.actor_id == user_id
                and run.workspace_id == action.workspace_id
            ):
                chat = session.get(TurnSession, run.session_id, with_for_update=True)
                summary = dict(run.result or {})
                summary.pop("pending_action", None)
                summary["status"] = "succeeded"
                summary["action_result"] = result
                summary["text"] = "操作已确认并保存。" if accept else "已取消保存，未执行此操作。"
                run.status = "succeeded"
                run.result = summary
                run.state = {
                    **(run.state or {}),
                    "pending_action_id": None,
                    "previous_action_result": result,
                }
                if chat is not None:
                    chat.state = {
                        **(chat.state or {}),
                        "pending_action_id": None,
                        "previous_action_result": result,
                    }
                append_event(session, run, "action_decided", result)
        session.add(
            AuditEvent(
                workspace_id=action.workspace_id,
                actor_id=user_id,
                action=f"action.{op}",
                object_id=action.id,
                detail={"type": action.type, "status": action.status},
            )
        )
        return result


def accessible_run(user_id: str, run_id: str) -> TurnRun:
    with SessionLocal() as session:
        run = session.get(TurnRun, run_id)
        if run is None or run.actor_id != user_id:
            raise HTTPException(404, detail={"code": "RUN_NOT_FOUND"})
        authorized_workspace(session, user_id, run.workspace_id)
        return run


def cancel_run(user_id: str, run_id: str) -> None:
    with SessionLocal() as session, session.begin():
        run = session.get(TurnRun, run_id, with_for_update=True)
        if run is None or run.actor_id != user_id:
            raise HTTPException(404, detail={"code": "RUN_NOT_FOUND"})
        authorized_workspace(session, user_id, run.workspace_id)
        if run.status in {"queued", "running"}:
            run.cancel_requested = True


def request_status(text: str) -> dict:
    if any(word in text for word in ("不吃", "过敏", "不要", "忌口")):
        return {
            "status": "waiting_input",
            "text": "请在家庭档案中确认本次成员范围及限制后再规划。",
            "warnings": ["本演示运行不具备完整家庭限制读取能力，因此不声称菜单符合限制。"],
        }
    if any(word in text for word in ("加入清单", "放进购物", "买点")):
        name = next((key for key in ("牛奶", "番茄", "鸡蛋", "豆腐") if key in text), None)
        if name:
            return {
                "status": "waiting_confirmation",
                "text": f"准备将{name}加入共享清单。",
                "action": {
                    "type": "add_procurement",
                    "payload": {
                        "name": name,
                        "ingredient_key": {
                            "番茄": "tomato",
                            "鸡蛋": "egg",
                            "豆腐": "tofu",
                            "牛奶": "milk",
                        }[name],
                        "quantity_status": "unknown",
                    },
                },
            }
    if any(word in text for word in ("冰箱", "库存", "还有")):
        return {
            "status": "succeeded",
            "text": "当前演示家庭没有可核验的库存批次记录；这不表示冰箱里一定没有。",
            "evidence": [],
        }
    return {
        "status": "succeeded",
        "text": "当前使用 MealFlow 本地 demo Provider（非真实模型）；"
        "仅提供已读取证据，不生成未经核验的菜谱结论。",
        "evidence": [],
        "provider": "local_demo",
        "fake": True,
    }
