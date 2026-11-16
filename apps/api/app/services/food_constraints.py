"""Deterministic matching of household restrictions against recipe ingredients.

The model may choose a recipe, but it never decides whether a restriction is hit.
Web fallback uses the same function; ``not_detected`` never means safe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.v2_models import HouseholdProfile
from app.services.ingredient_labels import clean_ingredient_names

TYPE_LABELS = {"allergy": "过敏", "dislike": "忌口", "legacy_free_text": "备注"}
_EXCLUSION = re.compile(r"(?:不吃|不要|忌口|排除)([^，。；！!?？\s]+)")
_SPLIT = re.compile(r"[、和与]")
_NORMALIZE = re.compile(r"[\s·,，、;；/]+")
_FOOD_ALIASES = (
    frozenset({"虾", "虾仁", "shrimp"}),
    frozenset({"鸡蛋", "egg"}),
    frozenset({"牛奶", "milk"}),
    frozenset({"花生", "花生米", "peanut"}),
)


def ingredient_terms(value: str) -> set[str]:
    normalized = _norm(value)
    return next((set(group) for group in _FOOD_ALIASES if normalized in group), {normalized})


def _norm(value: str) -> str:
    return _NORMALIZE.sub("", value.strip().lower())


def _canonical_terms(value: str) -> tuple[str, ...]:
    """Turn a free-text restriction label into terms that can match a food name.

    A bracketed note used to survive as part of the term ("花生（过敏）"), so
    `_matches` never found it inside "花生酱" and the allergy was missed.
    Cleaning must never lose a restriction, so an uncleanable label (a long
    custom key, a whole sentence) is kept as-is instead of being dropped.
    """
    names = clean_ingredient_names(value)
    if names:
        return tuple(_norm(name) for name in names)
    return (_norm(value),)


def _free_terms(text: str) -> tuple[str, ...]:
    parsed = [item for phrase in _EXCLUSION.findall(text) for item in _SPLIT.split(phrase) if item]
    if not parsed:
        return (text.strip(),)
    terms = [term for item in parsed for term in _canonical_terms(item)]
    return tuple(dict.fromkeys(term for term in terms if term)) or (text.strip(),)


@dataclass(frozen=True)
class HouseholdConstraint:
    member_id: str
    member_name: str
    type: str
    key: str
    label: str
    terms: tuple[str, ...]


@dataclass(frozen=True)
class ConstraintHit:
    member_id: str
    member_name: str
    type: str
    label: str


@dataclass
class ConstraintCheck:
    hits: list[ConstraintHit]
    has_constraints: bool
    reason: str = ""

    @property
    def blocked(self) -> bool:
        return any(hit.type == "allergy" for hit in self.hits)

    @property
    def status(self) -> str:
        if self.reason:
            return "cannot_determine"
        return "matched" if self.hits else "not_detected"

    def line(self) -> str:
        if self.reason:
            return self.reason
        if not self.hits:
            target = (
                "未命中已登记过敏/忌口" if self.has_constraints
                else "无已登记过敏/忌口"
            )
            conclusion = "通过"
        else:
            target = "；".join(
                f"{hit.member_name}·{TYPE_LABELS.get(hit.type, '备注')}·{hit.label}"
                for hit in dict.fromkeys(self.hits)
            )
            conclusion = "不可推荐" if self.blocked else "通过"
        return f"忌口校验：已对照：{target}；结论：{conclusion}"


def household_constraints(session: Session, workspace_id: str) -> list[HouseholdConstraint]:
    profiles = session.scalars(
        select(HouseholdProfile)
        .where(HouseholdProfile.workspace_id == workspace_id)
        .order_by(HouseholdProfile.display_name, HouseholdProfile.id)
    ).all()
    result: list[HouseholdConstraint] = []
    for profile in profiles:
        for raw in profile.restrictions or []:
            if isinstance(raw, str):
                text = raw.strip()
                if text:
                    result.append(HouseholdConstraint(
                        profile.id, profile.display_name, "legacy_free_text",
                        _norm(text), text, _free_terms(text),
                    ))
                continue
            if not isinstance(raw, dict):
                continue
            kind = raw.get("type")
            if kind in {"allergy", "dislike"}:
                key = str(raw.get("ingredient_key") or raw.get("ingredient_name") or "").strip()
                if not key:
                    continue
                label = str(raw.get("ingredient_name") or key).strip()
                terms = tuple(dict.fromkeys(
                    term
                    for term in (*_canonical_terms(key), *_canonical_terms(label))
                    if term
                ))
                result.append(HouseholdConstraint(
                    profile.id, profile.display_name, kind, _norm(key), label, terms,
                ))
            elif kind == "legacy_free_text":
                text = str(raw.get("text") or "").strip()
                if text:
                    result.append(HouseholdConstraint(
                        profile.id, profile.display_name, kind, _norm(text), text,
                        _free_terms(text),
                    ))
    return result


def _matches(constraint: HouseholdConstraint, value: str, *, exact_only: bool) -> bool:
    candidate = _norm(value)
    if not candidate:
        return False
    terms = {alias for term in constraint.terms for alias in ingredient_terms(term)}
    for term in terms:
        if not term:
            continue
        if candidate == term:
            return True
        if not exact_only and (len(term) >= 2 or term == "虾") and term in candidate:
            return True
    return False


def check_recipe(
    constraints: list[HouseholdConstraint],
    *,
    ingredient_names: list[str] | tuple[str, ...] = (),
    ingredient_keys: list[str] | tuple[str, ...] = (),
    allergens: list[str] | tuple[str, ...] = (),
    taste_tags: list[str] | tuple[str, ...] = (),
) -> ConstraintCheck:
    values = [
        *(("name", value) for value in ingredient_names),
        *(("key", value) for value in ingredient_keys),
        *(("allergen", value) for value in allergens),
        *(("tag", value) for value in taste_tags),
    ]
    hits: list[ConstraintHit] = []
    for constraint in constraints:
        for kind, value in values:
            # Free text and display names allow containment; keys/tags need equality.
            if _matches(constraint, value, exact_only=kind == "tag"):
                hits.append(ConstraintHit(
                    constraint.member_id, constraint.member_name,
                    constraint.type, constraint.label,
                ))
                break
    return ConstraintCheck(hits, bool(constraints))
