"""Cooking logs: household read/comment, creator-only edits, opt-in citation."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.v2_models import CookingLog, CookingLogComment, CookingLogDish, CookingLogPhoto
from app.services import photo_store


class CookingLogError(Exception):
    def __init__(self, status: int, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.detail = detail


def active_log(session: Session, workspace_id: str, log_id: str) -> CookingLog:
    log = session.get(CookingLog, log_id)
    if log is None or log.workspace_id != workspace_id or log.status != "active":
        raise CookingLogError(404, "COOKING_LOG_NOT_FOUND")
    return log


def _names(session: Session, user_ids: set[str]) -> dict[str, str]:
    if not user_ids:
        return {}
    rows = session.execute(select(User.id, User.display_name).where(User.id.in_(user_ids)))
    return dict(rows.all())


def _dishes(session: Session, log_ids: list[str]) -> dict[str, list[CookingLogDish]]:
    result: dict[str, list[CookingLogDish]] = {log_id: [] for log_id in log_ids}
    if not log_ids:
        return result
    rows = session.scalars(
        select(CookingLogDish)
        .where(CookingLogDish.log_id.in_(log_ids))
        .order_by(CookingLogDish.position, CookingLogDish.created_at)
    )
    for dish in rows:
        result[dish.log_id].append(dish)
    return result


def _photos(session: Session, log_ids: list[str]) -> dict[str, list[CookingLogPhoto]]:
    result: dict[str, list[CookingLogPhoto]] = {log_id: [] for log_id in log_ids}
    if not log_ids:
        return result
    rows = session.scalars(
        select(CookingLogPhoto)
        .where(CookingLogPhoto.log_id.in_(log_ids))
        .order_by(CookingLogPhoto.position, CookingLogPhoto.created_at)
    )
    for photo in rows:
        result[photo.log_id].append(photo)
    return result


def _comments(session: Session, log_id: str) -> list[CookingLogComment]:
    return list(
        session.scalars(
            select(CookingLogComment)
            .where(CookingLogComment.log_id == log_id)
            .order_by(CookingLogComment.created_at)
        )
    )


def _comment_counts(session: Session, log_ids: list[str]) -> dict[str, int]:
    if not log_ids:
        return {}
    rows = session.execute(
        select(CookingLogComment.log_id, func.count())
        .where(CookingLogComment.log_id.in_(log_ids))
        .group_by(CookingLogComment.log_id)
    )
    return dict(rows.all())


def _photo_url(workspace_id: str, log_id: str, photo_id: str) -> str:
    return f"/api/v2/households/{workspace_id}/cooking-logs/{log_id}/photos/{photo_id}"


def _payload(
    log: CookingLog,
    *,
    viewer_id: str,
    workspace_id: str,
    creator_name: str,
    dishes: list[CookingLogDish],
    photos: list[CookingLogPhoto],
    comments: list[CookingLogComment] | None = None,
    comment_count: int = 0,
    author_names: dict[str, str] | None = None,
) -> dict:
    names = author_names or {}
    return {
        "id": log.id,
        "workspace_id": log.workspace_id,
        "title": log.title,
        "cooked_at": log.cooked_at.isoformat(),
        "notes": log.notes,
        "allow_assistant_citation": log.allow_assistant_citation,
        "version": log.version,
        "creator": {"id": log.creator_id, "display_name": creator_name},
        "can_edit": log.creator_id == viewer_id,
        "dishes": [
            {
                "id": item.id,
                "recipe_id": item.recipe_id,
                "name": item.name,
                "note": item.note,
                "position": item.position,
            }
            for item in dishes
        ],
        "photos": [
            {
                "id": item.id,
                "url": _photo_url(workspace_id, log.id, item.id),
                "content_type": item.content_type,
                "byte_size": item.byte_size,
                "position": item.position,
            }
            for item in photos
        ],
        "comment_count": comment_count,
        "comments": [
            {
                "id": item.id,
                "author": {
                    "id": item.author_id,
                    "display_name": names.get(item.author_id, "已注销成员"),
                },
                "body": item.body,
                "created_at": item.created_at.isoformat(),
            }
            for item in (comments or [])
        ],
    }


def list_logs(
    session: Session,
    workspace_id: str,
    viewer_id: str,
    *,
    limit: int,
    offset: int,
    creator_id: str | None = None,
) -> list[dict]:
    query = select(CookingLog).where(
        CookingLog.workspace_id == workspace_id, CookingLog.status == "active"
    )
    if creator_id is not None:
        query = query.where(CookingLog.creator_id == creator_id)
    logs = list(
        session.scalars(
            query.order_by(CookingLog.cooked_at.desc(), CookingLog.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    log_ids = [item.id for item in logs]
    dishes = _dishes(session, log_ids)
    photos = _photos(session, log_ids)
    counts = _comment_counts(session, log_ids)
    names = _names(session, {item.creator_id for item in logs})
    return [
        _payload(
            item,
            viewer_id=viewer_id,
            workspace_id=workspace_id,
            creator_name=names.get(item.creator_id, "已注销成员"),
            dishes=dishes[item.id],
            photos=photos[item.id],
            comment_count=counts.get(item.id, 0),
        )
        for item in logs
    ]


def get_log(session: Session, workspace_id: str, log_id: str, viewer_id: str) -> dict:
    log = active_log(session, workspace_id, log_id)
    comments = _comments(session, log.id)
    names = _names(session, {log.creator_id, *(item.author_id for item in comments)})
    return _payload(
        log,
        viewer_id=viewer_id,
        workspace_id=workspace_id,
        creator_name=names.get(log.creator_id, "已注销成员"),
        dishes=_dishes(session, [log.id])[log.id],
        photos=_photos(session, [log.id])[log.id],
        comments=comments,
        comment_count=len(comments),
        author_names=names,
    )


def create_log(
    session: Session,
    *,
    workspace_id: str,
    creator_id: str,
    title: str,
    cooked_at: datetime,
    notes: str,
    allow_assistant_citation: bool,
    dishes: list[dict],
) -> dict:
    log = CookingLog(
        workspace_id=workspace_id,
        creator_id=creator_id,
        title=title,
        cooked_at=cooked_at,
        notes=notes,
        allow_assistant_citation=allow_assistant_citation,
    )
    session.add(log)
    session.flush()
    for position, item in enumerate(dishes):
        session.add(
            CookingLogDish(
                log_id=log.id,
                recipe_id=item.get("recipe_id"),
                name=item["name"],
                note=item.get("note", ""),
                position=position,
            )
        )
    session.commit()
    return get_log(session, workspace_id, log.id, creator_id)


def update_log(
    session: Session,
    *,
    workspace_id: str,
    log_id: str,
    editor_id: str,
    expected_version: int,
    title: str | None = None,
    cooked_at: datetime | None = None,
    notes: str | None = None,
    allow_assistant_citation: bool | None = None,
    dishes: list[dict] | None = None,
) -> dict:
    log = active_log(session, workspace_id, log_id)
    if log.creator_id != editor_id:
        raise CookingLogError(403, "CREATOR_ONLY")
    log = session.scalars(
        select(CookingLog).where(CookingLog.id == log_id).with_for_update()
    ).one()
    if log.version != expected_version:
        session.rollback()
        raise CookingLogError(409, "VERSION_CONFLICT", str(log.version))
    if title is not None:
        log.title = title
    if cooked_at is not None:
        log.cooked_at = cooked_at
    if notes is not None:
        log.notes = notes
    if allow_assistant_citation is not None:
        log.allow_assistant_citation = allow_assistant_citation
    if dishes is not None:
        session.execute(delete(CookingLogDish).where(CookingLogDish.log_id == log_id))
        for position, item in enumerate(dishes):
            session.add(
                CookingLogDish(
                    log_id=log_id,
                    recipe_id=item.get("recipe_id"),
                    name=item["name"],
                    note=item.get("note", ""),
                    position=position,
                )
            )
    log.version += 1
    session.commit()
    return get_log(session, workspace_id, log_id, editor_id)


def add_comment(
    session: Session, *, workspace_id: str, log_id: str, author_id: str, body: str
) -> dict:
    active_log(session, workspace_id, log_id)
    comment = CookingLogComment(log_id=log_id, author_id=author_id, body=body)
    session.add(comment)
    session.commit()
    session.refresh(comment)
    author = session.get(User, author_id)
    return {
        "id": comment.id,
        "author": {
            "id": author_id,
            "display_name": author.display_name if author else "已注销成员",
        },
        "body": comment.body,
        "created_at": comment.created_at.isoformat(),
    }


def add_photo(
    session: Session,
    *,
    workspace_id: str,
    log_id: str,
    editor_id: str,
    data: bytes,
) -> dict:
    log = active_log(session, workspace_id, log_id)
    if log.creator_id != editor_id:
        raise CookingLogError(403, "CREATOR_ONLY")
    stored = photo_store.store(data)
    log = session.scalars(
        select(CookingLog).where(CookingLog.id == log_id).with_for_update()
    ).one()
    last = session.scalar(
        select(func.max(CookingLogPhoto.position)).where(CookingLogPhoto.log_id == log_id)
    )
    photo = CookingLogPhoto(log_id=log_id, position=(last or -1) + 1, **stored)
    session.add(photo)
    log.version += 1
    try:
        session.commit()
    except Exception:  # Persist failure: remove the orphaned file before re-raising.
        session.rollback()
        photo_store.remove(stored["storage_key"])
        raise
    session.refresh(photo)
    return {
        "id": photo.id,
        "url": _photo_url(workspace_id, log_id, photo.id),
        "content_type": photo.content_type,
        "byte_size": photo.byte_size,
        "position": photo.position,
        "version": log.version,
    }


def delete_photo(
    session: Session, *, workspace_id: str, log_id: str, photo_id: str, editor_id: str
) -> dict:
    log = active_log(session, workspace_id, log_id)
    if log.creator_id != editor_id:
        raise CookingLogError(403, "CREATOR_ONLY")
    photo = session.get(CookingLogPhoto, photo_id)
    if photo is None or photo.log_id != log_id:
        raise CookingLogError(404, "COOKING_LOG_PHOTO_NOT_FOUND")
    key = photo.storage_key
    session.delete(photo)
    log.version += 1
    session.commit()
    photo_store.remove(key)
    return {"version": log.version}


def citable_logs(session: Session, workspace_id: str, *, limit: int = 20) -> list[dict]:
    """Return only logs the household explicitly enabled for assistant citation."""
    logs = list(
        session.scalars(
            select(CookingLog)
            .where(
                CookingLog.workspace_id == workspace_id,
                CookingLog.status == "active",
                CookingLog.allow_assistant_citation.is_(True),
            )
            .order_by(CookingLog.cooked_at.desc())
            .limit(limit)
        )
    )
    dishes = _dishes(session, [item.id for item in logs])
    names = _names(session, {item.creator_id for item in logs})
    return [
        {
            "id": item.id,
            "title": item.title,
            "cooked_at": item.cooked_at.isoformat(),
            "notes": item.notes,
            "creator": names.get(item.creator_id, "已注销成员"),
            "dishes": [dish.name for dish in dishes[item.id]],
            "citation": f"做饭记录《{item.title}》（{item.cooked_at.date().isoformat()}）",
        }
        for item in logs
    ]