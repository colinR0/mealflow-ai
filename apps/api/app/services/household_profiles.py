"""Household-scoped diner archives, independent of login accounts."""

from datetime import date
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.db.models import WorkspaceMember
from app.db.session import SessionLocal
from app.db.v2_models import HouseholdProfile
from app.services.v2_runtime import authorized_workspace


class ArchiveInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Restriction(ArchiveInput):
    type: Literal["allergy", "dislike", "legacy_free_text"]
    ingredient_key: str | None = Field(default=None, min_length=1, max_length=100)
    ingredient_name: str | None = Field(default=None, max_length=100)
    text: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_target(self):
        if self.type == "legacy_free_text" and not self.text:
            raise ValueError("A free-text restriction needs text")
        if self.type != "legacy_free_text" and not self.ingredient_key:
            raise ValueError("A restriction needs an ingredient")
        return self


class ProfileEvent(ArchiveInput):
    id: str | None = Field(default=None, max_length=80)
    event_type: str = Field(min_length=1, max_length=32)
    start_date: date
    end_date: date
    constraint_tags: list[str] = Field(default_factory=list, max_length=20)
    note: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def require_date_order(self):
        if self.end_date < self.start_date:
            raise ValueError("End date must not precede start date")
        if any(not tag.strip() or len(tag) > 40 for tag in self.constraint_tags):
            raise ValueError("Dietary tags must contain 1 to 40 characters")
        return self


class ProfileFields(ArchiveInput):
    name: str = Field(min_length=1, max_length=80)
    gender: Literal["男", "女", "保密"] = "保密"
    residency: Literal["resident", "guest"] = "resident"
    taboo: str = Field(default="", max_length=500)
    note: str = Field(default="", max_length=500)
    restrictions: list[Restriction | str] = Field(default_factory=list, max_length=100)
    events: list[ProfileEvent] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def require_restriction_text(self):
        if any(isinstance(item, str) and (not item.strip() or len(item) > 500)
               for item in self.restrictions):
            raise ValueError("Restriction text must contain 1 to 500 characters")
        return self


class ProfileCreate(ProfileFields):
    linked_user_id: str | None = None


class ProfileUpdate(ProfileFields):
    expected_version: int = Field(ge=1)


def archive_view(profile: HouseholdProfile) -> dict:
    details = profile.legacy_details or {}
    return {
        "id": profile.id,
        "display_name": profile.display_name,
        "linked_user_id": profile.linked_user_id,
        "gender": details.get("gender") or "保密",
        "residency": details.get("residency") or "resident",
        "taboo": details.get("taboo") or "",
        "note": details.get("note") or "",
        "events": details.get("events") or [],
        "restrictions": profile.restrictions,
        "preferences": profile.preferences,
        "legacy_details": details,
        "version": profile.version,
    }


def list_archives(user_id: str, workspace_id: str) -> list[dict]:
    with SessionLocal() as session:
        authorized_workspace(session, user_id, workspace_id)
        return [archive_view(profile) for profile in session.scalars(
            select(HouseholdProfile).where(HouseholdProfile.workspace_id == workspace_id)
            .order_by(HouseholdProfile.created_at, HouseholdProfile.id)
        )]


def apply_fields(profile: HouseholdProfile, body: ProfileFields) -> None:
    values = body.model_dump(mode="json")
    profile.display_name = body.name
    profile.restrictions = values["restrictions"]
    detail_keys = {"gender", "residency", "taboo", "note", "events"}
    profile.legacy_details = {
        **(profile.legacy_details or {}),
        **{key: value for key, value in values.items() if key in detail_keys},
    }


def create_archive(user_id: str, workspace_id: str, body: ProfileCreate) -> dict:
    with SessionLocal() as session, session.begin():
        authorized_workspace(session, user_id, workspace_id)
        if body.linked_user_id:
            member = session.scalar(select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == body.linked_user_id,
                WorkspaceMember.status == "active",
            ))
            if member is None:
                raise HTTPException(422, detail={"code": "PROFILE_USER_NOT_MEMBER"})
        profile = HouseholdProfile(
            workspace_id=workspace_id, display_name=body.name,
            linked_user_id=body.linked_user_id, restrictions=[], preferences=[],
        )
        apply_fields(profile, body)
        session.add(profile)
        session.flush()
        return archive_view(profile)


def update_archive(
    user_id: str, workspace_id: str, profile_id: str, body: ProfileUpdate,
) -> dict:
    with SessionLocal() as session, session.begin():
        authorized_workspace(session, user_id, workspace_id)
        profile = session.scalar(select(HouseholdProfile).where(
            HouseholdProfile.id == profile_id, HouseholdProfile.workspace_id == workspace_id,
        ).with_for_update())
        if profile is None:
            raise HTTPException(404, detail={"code": "PROFILE_NOT_FOUND"})
        if profile.version != body.expected_version:
            raise HTTPException(409, detail={"code": "PROFILE_VERSION_CONFLICT"})
        apply_fields(profile, body)
        profile.version += 1
        session.flush()
        return archive_view(profile)


def delete_archive(user_id: str, workspace_id: str, profile_id: str, version: int) -> None:
    with SessionLocal() as session, session.begin():
        authorized_workspace(session, user_id, workspace_id)
        profile = session.scalar(select(HouseholdProfile).where(
            HouseholdProfile.id == profile_id, HouseholdProfile.workspace_id == workspace_id,
        ).with_for_update())
        if profile is None:
            raise HTTPException(404, detail={"code": "PROFILE_NOT_FOUND"})
        if profile.version != version:
            raise HTTPException(409, detail={"code": "PROFILE_VERSION_CONFLICT"})
        session.delete(profile)
