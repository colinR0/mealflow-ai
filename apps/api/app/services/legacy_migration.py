"""Explicit, append-only imports. Legacy account credentials are never read or trusted."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Workspace
from app.db.v2_models import HouseholdProfile, InventoryLot, InventoryMovement, LegacyImport
from app.services.v2_runtime import authorized_workspace, canonical_hash


def read_source(path: Path, workspace_id: str) -> dict[str, list[dict]]:
    """Open SQLite read-only; no ORM/schema initialization against the original file."""
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        if db.execute("SELECT id FROM workspaces WHERE id=?", (workspace_id,)).fetchone() is None:
            raise ValueError("source household does not exist")
        members = [dict(row) for row in db.execute(
            "SELECT u.id, u.display_name, u.gender, u.residency, u.taboo, u.note "
            "FROM users u JOIN workspace_members m ON m.user_id=u.id "
            "WHERE m.workspace_id=?", (workspace_id,),
        )]
        inventory = [dict(row) for row in db.execute(
            "SELECT i.id, g.key ingredient_key, g.name, i.quantity, i.unit, "
            "i.expires_on, i.updated_at FROM inventory_items i "
            "LEFT JOIN ingredients g ON g.id=i.ingredient_id WHERE i.workspace_id=?",
            (workspace_id,),
        )]
        restrictions = [dict(row) for row in db.execute(
            "SELECT id,user_id,type,ingredient_key FROM dietary_restrictions "
            "WHERE workspace_id=?", (workspace_id,),
        )]
        preferences = [dict(row) for row in db.execute(
            "SELECT id,user_id,target_type,target_key,target_name,preference,source "
            "FROM member_food_preferences WHERE workspace_id=?", (workspace_id,),
        )]
        events = [dict(row) for row in db.execute(
            "SELECT id,user_id,event_type,constraint_tags,start_date,end_date,note "
            "FROM member_events WHERE workspace_id=?", (workspace_id,),
        )]
    return {"users": members, "inventory_items": inventory,
            "dietary_restrictions": restrictions, "member_food_preferences": preferences,
            "member_events": events}


def target_snapshot(row) -> dict:
    if isinstance(row, HouseholdProfile):
        return {"display_name": row.display_name, "legacy_user_id": row.legacy_user_id,
                "linked_user_id": row.linked_user_id, "restrictions": row.restrictions,
                "preferences": row.preferences, "legacy_details": row.legacy_details}
    return {"ingredient_key": row.ingredient_key, "name": row.name,
            "quantity": str(Decimal(str(row.quantity)).quantize(Decimal("0.001"))),
            "unit": row.unit, "quantity_status": row.quantity_status,
            "expires_on": row.expires_on.isoformat() if row.expires_on else None,
            "source_type": row.source_type, "source_id": row.source_id}


def migrate(session: Session, rows: dict, *, source: str, workspace_id: str,
            actor_id: str, reconcile: bool = False) -> dict:
    """Caller owns transaction. Never overwrite v2 edits or silently refresh imports."""
    if not source or len(source) > 120:
        raise ValueError("source must be a stable identifier of 1..120 characters")
    authorized_workspace(session, actor_id, workspace_id, owner=True)
    session.get(Workspace, workspace_id, with_for_update=True)
    reports = {"created": 0, "matched": 0, "issues": [], "counts": {
        key: len(value) for key, value in rows.items()}}
    grouped = {row["id"]: row for row in rows["users"]}
    for table in ("dietary_restrictions", "member_food_preferences", "member_events"):
        for row in rows.get(table, []):
            if row["user_id"] is not None and row["user_id"] not in grouped:
                reports["issues"].append({"table": table, "id": row["id"],
                                          "reason": "unmapped_member"})
    units = []
    for user in rows["users"]:
        snapshot = {**user, "restrictions": [
            {**r, "source_table": "dietary_restrictions"}
            for r in rows["dietary_restrictions"] if r["user_id"] in (None, user["id"])
        ], "preferences": [r for r in rows.get("member_food_preferences", [])
                           if r["user_id"] == user["id"]],
            "events": [r for r in rows.get("member_events", [])
                       if r["user_id"] == user["id"]]}
        units.append(("users", user["id"], snapshot))
    # A dedicated unlinked profile preserves shared restrictions even in an empty household.
    shared = [r for r in rows["dietary_restrictions"] if r["user_id"] is None]
    if shared:
        units.append(("dietary_restrictions", "shared", {"restrictions": shared}))
    units.extend(("inventory_items", row["id"], row) for row in rows["inventory_items"])
    seen = set()
    for table, source_id, snapshot in units:
        seen.add((table, source_id))
        previous = session.scalar(select(LegacyImport).where(
            LegacyImport.workspace_id == workspace_id, LegacyImport.source == source,
            LegacyImport.source_table == table, LegacyImport.source_id == source_id,
        ))
        if previous:
            model = InventoryLot if previous.target_table == "inventory_lots" else HouseholdProfile
            target = session.get(model, previous.target_id)
            reason = ("source_changed" if previous.source_hash != canonical_hash(snapshot)
                      else "target_drift" if target is None or
                      previous.target_hash != canonical_hash(target_snapshot(target)) else None)
            if reason:
                reports["issues"].append({"table": table, "id": source_id, "reason": reason})
            else:
                reports["matched"] += 1
            continue
        if reconcile:
            reports["issues"].append({"table": table, "id": source_id, "reason": "not_imported"})
            continue
        if table == "inventory_items":
            if not snapshot["ingredient_key"] or not snapshot["name"]:
                reports["issues"].append({"table": table, "id": source_id,
                                          "reason": "unmapped_ingredient"})
                continue
            quantity = Decimal(str(snapshot["quantity"]))
            if not quantity.is_finite() or quantity < 0 or not snapshot["unit"].strip():
                reports["issues"].append({"table": table, "id": source_id,
                                          "reason": "invalid_quantity"})
                continue
            target = InventoryLot(
                workspace_id=workspace_id, ingredient_key=snapshot["ingredient_key"],
                name=snapshot["name"], quantity_status="known", quantity=quantity,
                unit=snapshot["unit"], expires_on=date.fromisoformat(snapshot["expires_on"])
                if snapshot["expires_on"] else None,
                confirmed_at=datetime.fromisoformat(snapshot["updated_at"]),
                source_type="legacy", source_id=canonical_hash([source, table, source_id]),
            )
        else:
            restrictions = snapshot["restrictions"]
            if snapshot.get("taboo"):
                restrictions = [*restrictions, {"type": "legacy_free_text",
                                                "text": snapshot["taboo"]}]
            target = HouseholdProfile(
                workspace_id=workspace_id,
                display_name=snapshot.get("display_name", "家庭共享限制"),
                legacy_user_id=snapshot.get("id"), linked_user_id=None,
                restrictions=restrictions, preferences=snapshot.get("preferences", []),
                legacy_details={key: snapshot.get(key) for key in
                                ("gender", "residency", "note", "events")},
            )
        session.add(target)
        session.flush()
        session.add(LegacyImport(
            workspace_id=workspace_id, source=source, source_table=table, source_id=source_id,
            source_hash=canonical_hash(snapshot), snapshot=snapshot,
            target_table=target.__tablename__, target_id=target.id,
            target_hash=canonical_hash(target_snapshot(target)),
        ))
        if isinstance(target, InventoryLot):
            session.add(InventoryMovement(
                workspace_id=workspace_id, lot_id=target.id, actor_id=actor_id,
                kind="legacy_import", before={}, after=target_snapshot(target),
                source_type="legacy", source_id=target.source_id,
            ))
        reports["created"] += 1
    for record in session.scalars(select(LegacyImport).where(
        LegacyImport.workspace_id == workspace_id, LegacyImport.source == source,
    )):
        if (record.source_table, record.source_id) not in seen:
            reports["issues"].append({"table": record.source_table, "id": record.source_id,
                                      "reason": "source_missing"})
    if shared and not rows["users"]:
        reports["issues"].append({"reason": "shared_restrictions_without_members"})
    session.flush()
    return reports
