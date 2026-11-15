"""Household runtime tables used by the current app and six tools."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models import TimestampMixin, new_id


class AuthSession(TimestampMixin, Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    native_client: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idle_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class RuntimeAdmission(Base):
    __tablename__ = "runtime_admission"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)


@event.listens_for(RuntimeAdmission.__table__, "after_create")
def seed_runtime_admission(table, connection, **kwargs):
    connection.execute(table.insert().values(id="agent_turn"))


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    window_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HouseholdProfile(TimestampMixin, Base):
    __tablename__ = "household_profiles"
    __table_args__ = (UniqueConstraint("workspace_id", "id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    linked_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    legacy_user_id: Mapped[str | None] = mapped_column(String(36))
    legacy_details: Mapped[dict] = mapped_column(JSON, default=dict)
    restrictions: Mapped[list] = mapped_column(JSON, default=list)
    preferences: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)


class HouseholdInvite(TimestampMixin, Base):
    __tablename__ = "household_invites"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InventoryLot(TimestampMixin, Base):
    __tablename__ = "inventory_lots"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "source_type", "source_id"),
        # One logical stock row per household ingredient.
        UniqueConstraint(
            "workspace_id", "ingredient_id", name="uq_inventory_lots_workspace_ingredient"
        ),
        CheckConstraint(
            "(quantity_status = 'unknown' AND quantity IS NULL AND unit IS NULL) OR "
            "(quantity_status = 'known' AND quantity IS NOT NULL "
            "AND quantity >= 0 AND unit IS NOT NULL)",
            name="ck_lot_quantity",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    ingredient_id: Mapped[str] = mapped_column(ForeignKey("ingredients.id"), index=True)
    ingredient_key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(120))
    quantity_status: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    unit: Mapped[str | None] = mapped_column(String(20))
    expires_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(String(300), default="")
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_type: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="active")


class InventoryMovement(TimestampMixin, Base):
    __tablename__ = "inventory_movements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "lot_id"], ["inventory_lots.workspace_id", "inventory_lots.id"]
        ),
        UniqueConstraint("workspace_id", "source_type", "source_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(36))
    lot_id: Mapped[str] = mapped_column(String(36))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(32))
    before: Mapped[dict] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    source_type: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str] = mapped_column(String(120))


class ShoppingItem(TimestampMixin, Base):
    __tablename__ = "shopping_items"
    __table_args__ = (UniqueConstraint("workspace_id", "id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    ingredient_key: Mapped[str] = mapped_column(String(80))
    quantity_status: Mapped[str] = mapped_column(String(16), default="unknown")
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    unit: Mapped[str | None] = mapped_column(String(20))
    note: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    sources: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)


class TurnSession(TimestampMixin, Base):
    __tablename__ = "turn_sessions"
    __table_args__ = (UniqueConstraint("workspace_id", "id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    active_run_id: Mapped[str | None] = mapped_column(String(36))


class TurnRun(TimestampMixin, Base):
    __tablename__ = "agent_turn_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(
            ["workspace_id", "session_id"], ["turn_sessions.workspace_id", "turn_sessions.id"]
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    session_id: Mapped[str] = mapped_column(String(36))
    parent_run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_turn_runs.id"))
    request: Mapped[dict] = mapped_column(JSON)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    next_seq: Mapped[int] = mapped_column(Integer, default=0)
    events_expire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))


class AgentEvent(Base):
    __tablename__ = "agent_events"
    __table_args__ = (UniqueConstraint("run_id", "seq"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_turn_runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ToolExecution(TimestampMixin, Base):
    __tablename__ = "tool_executions"
    __table_args__ = (UniqueConstraint("run_id", "tool_call_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_turn_runs.id"), index=True)
    tool_call_id: Mapped[str] = mapped_column(String(120))
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


class PendingAction(TimestampMixin, Base):
    __tablename__ = "pending_actions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "run_id"], ["agent_turn_runs.workspace_id", "agent_turn_runs.id"]
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str | None] = mapped_column(String(36))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class BackgroundJob(TimestampMixin, Base):
    __tablename__ = "background_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(32))
    business_id: Mapped[str] = mapped_column(String(36))
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(80))


class IdempotencyRecord(TimestampMixin, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("workspace_id", "actor_id", "operation", "key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    operation: Mapped[str] = mapped_column(String(80))
    key: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)


class AuditEvent(TimestampMixin, Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(80))
    object_id: Mapped[str] = mapped_column(String(36))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
