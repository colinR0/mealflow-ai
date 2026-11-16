"""Store still-photo detections until a person confirms them into inventory."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import new_id
from app.db.v2_models import FridgeObservation, InventoryLot
from app.services import fridge_vision, ingredient_catalog, inventory_lots, photo_store
from app.services.ingredient_labels import clean_ingredient_names


class ObservationError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        self.message = message
        super().__init__(message or code)


def view(row: FridgeObservation) -> dict:
    return {
        "id": row.id,
        "status": row.status,
        "width": row.width,
        "height": row.height,
        "image_url": (
            f"/api/v2/households/{row.workspace_id}/fridge-observations/{row.id}/image"
        ),
        "items": row.items,
        "created_at": row.created_at.isoformat(),
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
    }


def drop_if_unreferenced(session: Session, key: str) -> None:
    used = session.scalar(
        select(func.count())
        .select_from(FridgeObservation)
        .where(FridgeObservation.storage_key == key)
    )
    if not used:
        photo_store.remove(key)


def save_observation(
    session: Session,
    *,
    workspace_id: str,
    actor_id: str,
    stored: dict,
    detected: dict,
) -> dict:
    items = [{"id": new_id(), **item} for item in detected["items"]]
    row = FridgeObservation(
        workspace_id=workspace_id,
        created_by=actor_id,
        status="pending",
        storage_key=stored["storage_key"],
        content_type=stored["content_type"],
        width=detected["width"],
        height=detected["height"],
        items=items,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return view(row)


def list_observations(session: Session, workspace_id: str, *, status: str) -> list[dict]:
    rows = session.scalars(
        select(FridgeObservation)
        .where(
            FridgeObservation.workspace_id == workspace_id,
            FridgeObservation.status == status,
        )
        .order_by(FridgeObservation.created_at.desc())
        .limit(10)
    )
    return [view(row) for row in rows]


def get_observation(session: Session, workspace_id: str, observation_id: str) -> FridgeObservation:
    row = session.get(FridgeObservation, observation_id)
    if row is None or row.workspace_id != workspace_id:
        raise ObservationError("OBSERVATION_NOT_FOUND", "识别结果不存在")
    return row


def confirm_observation(
    session: Session,
    *,
    workspace_id: str,
    actor_id: str,
    observation_id: str,
    edits: list[dict],
) -> dict:
    row = session.scalar(
        select(FridgeObservation)
        .where(
            FridgeObservation.id == observation_id,
            FridgeObservation.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    if row is None:
        raise ObservationError("OBSERVATION_NOT_FOUND", "识别结果不存在")
    if row.status != "pending":
        raise ObservationError("OBSERVATION_CLOSED", "这份识别结果已经处理过")
    by_id = {item["id"]: item for item in row.items}
    chosen: list[tuple[str, dict]] = []
    for edit in edits:
        item = by_id.get(edit["id"])
        if item is None:
            raise ObservationError("INVALID_NAME", "识别项不存在")
        if not edit["kept"]:
            continue
        names = clean_ingredient_names(edit["name"])
        if len(names) != 1:
            raise ObservationError("INVALID_NAME", "请保留单一食材名")
        chosen.append((names[0], item))
    if not chosen:
        raise ObservationError("OBSERVATION_EMPTY", "请至少保留一种食材")

    lots = []
    seen: set[str] = set()
    updated = []
    kept_ids = {item["id"] for _, item in chosen}
    for name, item in chosen:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        ingredient = ingredient_catalog.resolve_ingredient(session, name=name)
        if ingredient.id in {lot["ingredient_id"] for lot in lots}:
            continue
        existing = session.scalar(
            select(InventoryLot).where(
                InventoryLot.workspace_id == workspace_id,
                InventoryLot.ingredient_id == ingredient.id,
                InventoryLot.status == "active",
                InventoryLot.quantity_status == "known",
            )
        )
        # A known quantity already on the shelf stays as the person recorded it.
        if existing is not None:
            lots.append(inventory_lots.lot_view(existing))
            continue
        lot_view, _outcome = inventory_lots.upsert_lot(
            session,
            workspace_id=workspace_id,
            actor_id=actor_id,
            ingredient=ingredient,
            name=name,
            quantity_status="unknown",
            quantity=None,
            unit=None,
            expires_on=None,
            notes="冰箱画面识别确认，数量待核对",
        )
        lots.append(lot_view)
    for item in row.items:
        copied = dict(item)
        match = next((name for name, source in chosen if source["id"] == item["id"]), None)
        copied["kept"] = item["id"] in kept_ids
        if match:
            copied["name"] = match
        updated.append(copied)
    row.items = updated
    row.status = "confirmed"
    row.confirmed_at = datetime.now(UTC)
    session.commit()
    session.refresh(row)
    return {"observation": view(row), "lots": lots}


def discard_observation(session: Session, workspace_id: str, observation_id: str) -> dict:
    row = get_observation(session, workspace_id, observation_id)
    if row.status != "pending":
        raise ObservationError("OBSERVATION_CLOSED", "这份识别结果已经处理过")
    row.status = "discarded"
    session.commit()
    session.refresh(row)
    return view(row)
