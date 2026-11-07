"""Inventory lot upsert against the stable ingredient catalog."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Ingredient
from app.db.v2_models import InventoryLot, InventoryMovement


def lot_view(row: InventoryLot) -> dict[str, Any]:
    return {
        "id": row.id,
        "ingredient_id": row.ingredient_id,
        "name": row.name,
        "ingredient_key": row.ingredient_key,
        "quantity_status": row.quantity_status,
        "quantity": str(row.quantity) if row.quantity is not None else None,
        "unit": row.unit,
        "expires_on": row.expires_on.isoformat() if row.expires_on else None,
        "notes": row.notes,
        "status": row.status,
        "confirmed_at": row.confirmed_at.isoformat(),
        "version": row.version,
    }


def add_movement(
    session: Session,
    actor_id: str,
    row: InventoryLot,
    kind: str,
    before: dict[str, Any],
) -> None:
    session.add(
        InventoryMovement(
            workspace_id=row.workspace_id,
            lot_id=row.id,
            actor_id=actor_id,
            kind=kind,
            before=before,
            after=lot_view(row),
            source_type="mobile",
            source_id=uuid4().hex,
        )
    )


def _lock_ingredient_lot(session: Session, workspace_id: str, ingredient_id: str) -> None:
    """Serialize upserts for one workspace+ingredient without locking other keys.

    SELECT FOR UPDATE does not block absent rows, so first-create races need a
    transaction-scoped advisory lock keyed only on this pair.
    """
    session.execute(
        text(
            "SELECT pg_advisory_xact_lock(hashtext(:workspace_id), hashtext(:ingredient_id))"
        ),
        {"workspace_id": workspace_id, "ingredient_id": ingredient_id},
    )


def _load_lot_for_update(
    session: Session, workspace_id: str, ingredient_id: str
) -> InventoryLot | None:
    return session.scalar(
        select(InventoryLot)
        .where(
            InventoryLot.workspace_id == workspace_id,
            InventoryLot.ingredient_id == ingredient_id,
        )
        .with_for_update()
    )


def _apply_upsert_fields(
    row: InventoryLot,
    *,
    ingredient: Ingredient,
    display_name: str,
    quantity_status: str,
    quantity,
    unit: str | None,
    expires_on,
    notes: str,
) -> None:
    row.ingredient_key = ingredient.key
    row.name = display_name
    row.quantity_status = quantity_status
    row.quantity = quantity
    row.unit = unit
    row.expires_on = expires_on
    row.notes = notes
    if row.quantity_status == "unknown":
        row.quantity = None
        row.unit = None
    row.status = "active"
    row.confirmed_at = datetime.now(UTC)


def upsert_lot(
    session: Session,
    *,
    workspace_id: str,
    actor_id: str,
    ingredient: Ingredient,
    name: str,
    quantity_status: str,
    quantity,
    unit: str | None,
    expires_on,
    notes: str,
) -> tuple[dict[str, Any], str]:
    """Create or overwrite the single logical lot for workspace+ingredient.

    Quantity is replaced, never accumulated. Consumed/removed rows reactivate here only.
    """
    _lock_ingredient_lot(session, workspace_id, ingredient.id)
    row = _load_lot_for_update(session, workspace_id, ingredient.id)
    display_name = name.strip() or ingredient.name
    if row is None:
        # Nested savepoint keeps the outer transaction usable if a unique race still hits.
        try:
            with session.begin_nested():
                row = InventoryLot(
                    workspace_id=workspace_id,
                    ingredient_id=ingredient.id,
                    ingredient_key=ingredient.key,
                    name=display_name,
                    quantity_status=quantity_status,
                    quantity=quantity,
                    unit=unit,
                    expires_on=expires_on,
                    notes=notes,
                    confirmed_at=datetime.now(UTC),
                    source_type="mobile",
                    source_id=uuid4().hex,
                    version=1,
                    status="active",
                )
                if row.quantity_status == "unknown":
                    row.quantity = None
                    row.unit = None
                session.add(row)
                session.flush()
                add_movement(session, actor_id, row, "added", {})
                return lot_view(row), "created"
        except IntegrityError:
            row = _load_lot_for_update(session, workspace_id, ingredient.id)
            if row is None:
                raise

    before = lot_view(row)
    restored = row.status in {"consumed", "removed"}
    _apply_upsert_fields(
        row,
        ingredient=ingredient,
        display_name=display_name,
        quantity_status=quantity_status,
        quantity=quantity,
        unit=unit,
        expires_on=expires_on,
        notes=notes,
    )
    row.version += 1
    kind = "restored" if restored else "updated"
    add_movement(session, actor_id, row, kind, before)
    return lot_view(row), ("restored" if restored else "updated")
