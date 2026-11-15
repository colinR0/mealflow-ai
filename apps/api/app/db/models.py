from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


class WorkspaceKind(StrEnum):
    ORGANIZATION = "organization"
    PERSONAL = "personal"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Workspace(TimestampMixin, Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(32), default=WorkspaceKind.ORGANIZATION.value)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str | None] = mapped_column(String(160), unique=True, index=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(11), unique=True, index=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(80))
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True)
    residency: Mapped[str] = mapped_column(String(10), default="resident")
    taboo: Mapped[str | None] = mapped_column(String(200), nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)


class WorkspaceMember(TimestampMixin, Base):
    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_membership"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(32), default="user")
    status: Mapped[str] = mapped_column(String(32), default="active")


class Ingredient(TimestampMixin, Base):
    __tablename__ = "ingredients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(60))
    default_unit: Mapped[str] = mapped_column(String(20))
    reference_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    price_source: Mapped[str] = mapped_column(String(120), default="市区大型超市公开参考价")
    price_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class IngredientAlias(TimestampMixin, Base):
    __tablename__ = "ingredient_aliases"
    __table_args__ = (UniqueConstraint("normalized_alias"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ingredient_id: Mapped[str] = mapped_column(ForeignKey("ingredients.id"), index=True)
    alias: Mapped[str] = mapped_column(String(120))
    normalized_alias: Mapped[str] = mapped_column(String(120), index=True)


from app.db import source_registry_models as source_registry_models  # noqa: E402,F401
from app.db import v2_models as v2_models  # noqa: E402,F401
