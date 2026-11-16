"""Offline bundle exported from typed SQL, never from raw source JSON."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.services.food_constraints import household_constraints
from app.services.native_retrieval import tokenize_chinese
from app.services.recipe_actions import DISCLAIMER, data_version, recipe_payloads, skill_payloads


def _search_tokens(*parts: object) -> list[str]:
    text = " ".join(str(part) for part in parts if part)
    return list(dict.fromkeys(tokenize_chinese(text).split()))[:256]


def build_bundle(session: Session, workspace_id: str) -> dict:
    recipes = recipe_payloads(session)
    for recipe in recipes:
        recipe["search_tokens"] = _search_tokens(
            recipe["name"], recipe["category"], *recipe["tags"], *recipe["taste_tags"],
            *(item["name"] for item in recipe["ingredients"]), *recipe["steps"][:3],
        )
    skills = skill_payloads(session)
    for skill in skills:
        skill["search_tokens"] = _search_tokens(skill["title"], skill["text"])
    constraints = [asdict(item) for item in household_constraints(session, workspace_id)]
    version = data_version({"recipes": recipes, "skills": skills, "constraints": constraints})
    return {
        "schema_version": 1,
        "data_version": version,
        "generated_at": datetime.now(UTC).isoformat(),
        "recipes": recipes,
        "skills": skills,
        "constraints": constraints,
        "disclaimer": DISCLAIMER,
        "offline_rules": {
            "mode": "lexical_only",
            "search": "lexical_tokens",
            "generation": False,
            "writes": "idempotency_key_and_expected_version",
            "conflict": "409_no_auto_merge",
        },
    }