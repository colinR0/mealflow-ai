"""Import structured substitution, flavor-template and food-risk rows.

The input is JSON with three arrays. Every row keeps typed decision fields plus a
separate rag_text used only for embedding. Approved rows require a named source;
risk rows with unverified evidence cannot be approved by this importer.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.db.session import SessionLocal
from app.db.v2_models import (
    EmbeddingProfile,
    FlavorTemplate,
    FoodRiskRule,
    IngredientSubstitution,
)
from app.services.native_retrieval import tokenize_chinese, vector_literal
from import_howtocook_rag import DIMENSIONS, EMBED, PROFILE, PROVIDER, prepare_import

REVIEW_STATUSES = {"draft", "approved", "revoked"}
MODELS = {
    "substitutions": IngredientSubstitution,
    "flavor_templates": FlavorTemplate,
    "food_risks": FoodRiskRule,
}
ID_PREFIX = {
    "substitutions": "substitution",
    "flavor_templates": "flavor",
    "food_risks": "risk",
}


def stable_id(section: str, key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"mealflow:{ID_PREFIX[section]}:v1:{key}"))


def _text(value: Any, field: str, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{field} is required")
    return value


def _list(value: Any, field: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"{field} must be an array")
    return value


def _common(section: str, raw: dict) -> dict:
    key = _text(raw.get("key"), "key", required=True)
    status = _text(raw.get("review_status") or "draft", "review_status")
    if status not in REVIEW_STATUSES:
        raise ValueError(f"invalid review_status for {key}")
    source_name = _text(raw.get("source_name"), "source_name")
    if status == "approved" and not source_name:
        raise ValueError(f"approved {key} requires source_name")
    return {
        "id": stable_id(section, key),
        "review_status": status,
        "source_name": source_name,
        "source_url": _text(raw.get("source_url"), "source_url") or None,
        "notes": _text(raw.get("notes"), "notes"),
    }


def _substitution(raw: dict) -> dict:
    common = _common("substitutions", raw)
    from_key = _text(raw.get("from_ingredient_key"), "from_ingredient_key", required=True)
    to_key = _text(raw.get("to_ingredient_key"), "to_ingredient_key", required=True)
    from_name = _text(raw.get("from_ingredient_name"), "from_ingredient_name", required=True)
    to_name = _text(raw.get("to_ingredient_name"), "to_ingredient_name", required=True)
    try:
        factor = float(raw.get("conversion_factor", 1))
    except (TypeError, ValueError):
        raise ValueError(f"invalid conversion_factor for {from_key}") from None
    if factor <= 0:
        raise ValueError(f"conversion_factor must be positive for {from_key}")
    unit = _text(raw.get("unit") or "份", "unit")
    rag_text = _text(raw.get("rag_text"), "rag_text")
    if not rag_text:
        rag_text = (
            f"食材替换：{from_name} 可以替换为 {to_name}。"
            f"换算系数 {factor:g}，单位 {unit}。"
            f"风味影响：{_text(raw.get('flavor_impact'), 'flavor_impact')}。"
        )
    return {
        **common,
        "from_ingredient_key": from_key,
        "from_ingredient_name": from_name,
        "to_ingredient_key": to_key,
        "to_ingredient_name": to_name,
        "conversion_factor": factor,
        "unit": unit,
        "diet_tags": _list(raw.get("diet_tags"), "diet_tags"),
        "exclude_tags": _list(raw.get("exclude_tags"), "exclude_tags"),
        "flavor_impact": _text(raw.get("flavor_impact"), "flavor_impact"),
        "priority": int(raw.get("priority", 0)),
        "rag_text": rag_text,
    }


def _flavor(raw: dict) -> dict:
    common = _common("flavor_templates", raw)
    name = _text(raw.get("name"), "name", required=True)
    components = _list(raw.get("components"), "components")
    if common["review_status"] == "approved" and not components:
        raise ValueError(f"approved flavor template {name} requires components")
    normalized = []
    for component in components:
        if not isinstance(component, dict):
            raise TypeError(f"invalid component in {name}")
        try:
            parts = float(component.get("parts"))
        except (TypeError, ValueError):
            raise ValueError(f"invalid component parts in {name}") from None
        if parts <= 0:
            raise ValueError(f"component parts must be positive in {name}")
        normalized.append({
            "ingredient_key": _text(component.get("ingredient_key"), "ingredient_key",
                                    required=True),
            "name": _text(component.get("name"), "component name", required=True),
            "parts": parts,
            "unit": _text(component.get("unit") or "份", "component unit"),
            "role": _text(component.get("role"), "component role"),
        })
    ratio_basis = _text(raw.get("ratio_basis") or "parts", "ratio_basis")
    if ratio_basis not in {"parts", "weight", "volume"}:
        raise ValueError(f"invalid ratio_basis for {name}")
    rag_text = _text(raw.get("rag_text"), "rag_text")
    if not rag_text:
        ratio = "；".join(
            f"{item['name']} {item['parts']:g}{item['unit']}" for item in normalized
        ) or "待补充审核比例"
        rag_text = f"风味模板：{name}。固定配比：{ratio}。基准：{ratio_basis}。"
    return {
        **common,
        "name": name,
        "aliases": _list(raw.get("aliases"), "aliases"),
        "flavor_tags": _list(raw.get("flavor_tags"), "flavor_tags"),
        "components": normalized,
        "ratio_basis": ratio_basis,
        "usage": _text(raw.get("usage"), "usage"),
        "cautions": _list(raw.get("cautions"), "cautions"),
        "rag_text": rag_text,
    }


def _risk(raw: dict) -> dict:
    common = _common("food_risks", raw)
    risk_type = _text(raw.get("risk_type"), "risk_type", required=True)
    title = _text(raw.get("title"), "title", required=True)
    severity = _text(raw.get("severity") or "info", "severity")
    if severity not in {"info", "warning", "danger"}:
        raise ValueError(f"invalid severity for {title}")
    evidence = _text(raw.get("evidence_level") or "unverified", "evidence_level")
    if evidence not in {"established", "limited", "unverified"}:
        raise ValueError(f"invalid evidence_level for {title}")
    if common["review_status"] == "approved" and evidence == "unverified":
        raise ValueError(f"approved risk {title} cannot have unverified evidence")
    applies_to = _list(raw.get("applies_to"), "applies_to")
    if common["review_status"] == "approved" and not applies_to:
        raise ValueError(f"approved risk {title} requires applies_to")
    rag_text = _text(raw.get("rag_text"), "rag_text")
    if not rag_text:
        rag_text = (
            f"食品安全风险：{title}。类型：{risk_type}。等级：{severity}。"
            f"适用人群：{', '.join(map(str, applies_to)) or '未指定'}。"
            f"建议：{_text(raw.get('advice'), 'advice')}。"
        )
    return {
        **common,
        "risk_type": risk_type,
        "title": title,
        "severity": severity,
        "applies_to": applies_to,
        "min_age_months": raw.get("min_age_months"),
        "max_age_months": raw.get("max_age_months"),
        "condition_tags": _list(raw.get("condition_tags"), "condition_tags"),
        "ingredient_keys": _list(raw.get("ingredient_keys"), "ingredient_keys"),
        "advice": _text(raw.get("advice"), "advice"),
        "evidence_level": evidence,
        "rag_text": rag_text,
    }


BUILDERS = {
    "substitutions": _substitution,
    "flavor_templates": _flavor,
    "food_risks": _risk,
}


def build_plan(source: Path) -> list[dict]:
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("Source must be a JSON object")
    plan = []
    for section, builder in BUILDERS.items():
        rows = data.get(section, [])
        if not isinstance(rows, list):
            raise TypeError(f"{section} must be an array")
        for raw in rows:
            if not isinstance(raw, dict):
                raise TypeError(f"{section} entries must be objects")
            plan.append({"section": section, **builder(raw)})
    keys = [(item["section"], item["id"]) for item in plan]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate stable keys in input")
    return plan


def _apply_row(session, item: dict) -> tuple[bool, bool]:
    section = item["section"]
    model = MODELS[section]
    payload = {key: value for key, value in item.items() if key not in {"section", "id"}}
    row = session.get(model, item["id"])
    created = row is None
    if created:
        row = model(id=item["id"], embedding_profile_id=PROFILE, version=1, **payload)
        session.add(row)
        changed = True
    else:
        changed = any(getattr(row, key) != value for key, value in payload.items())
        if changed:
            row.version += 1
            for key, value in payload.items():
                setattr(row, key, value)
    row.embedding_profile_id = PROFILE
    row.embedding = list(EMBED(row.rag_text))
    session.flush()
    session.execute(
        text(f"""
            UPDATE {MODELS[section].__tablename__}
            SET embedding_vector = CAST(:vector AS vector),
                search_tsv = to_tsvector('simple', :tokens)
            WHERE id = :id
        """),
        {
            "vector": vector_literal(row.embedding),
            "tokens": tokenize_chinese(row.rag_text),
            "id": row.id,
        },
    )
    return created, changed and not created


def apply_plan(plan: list[dict]) -> dict:
    prepare_import()
    counts = {"created": 0, "updated": 0}
    with SessionLocal.begin() as session:
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext('structured-food-import'))"))
        profile = session.get(EmbeddingProfile, PROFILE)
        identity = (PROVIDER.model, PROVIDER.dimensions, PROVIDER.template_version,
                    PROVIDER.tokenizer_version, PROVIDER.fake)
        if profile is None:
            session.add(EmbeddingProfile(
                id=PROFILE, model=PROVIDER.model, dimensions=PROVIDER.dimensions,
                template_version=PROVIDER.template_version,
                tokenizer_version=PROVIDER.tokenizer_version, is_fake=PROVIDER.fake,
            ))
        elif (profile.model, profile.dimensions, profile.template_version,
              profile.tokenizer_version, profile.is_fake) != identity:
            raise RuntimeError("Existing embedding profile differs from configured provider")
        for item in plan:
            created, updated = _apply_row(session, item)
            counts["created"] += int(created)
            counts["updated"] += int(updated)
    return counts


def verify() -> dict:
    with SessionLocal() as session:
        result = {}
        for section, model in MODELS.items():
            total, approved, valid = session.execute(text(f"""
                SELECT count(*),
                       count(*) FILTER (WHERE review_status = 'approved'),
                       count(*) FILTER (
                         WHERE embedding_vector IS NOT NULL
                           AND vector_dims(embedding_vector) = :dimensions
                           AND search_tsv IS NOT NULL
                       )
                FROM {model.__tablename__}
            """), {"dimensions": DIMENSIONS}).one()
            result[section] = {
                "rows": total,
                "approved": approved,
                f"indexed_{DIMENSIONS}d": valid,
            }
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    plan = build_plan(args.source)
    report = {
        "rows": len(plan),
        "by_section": {
            section: sum(item["section"] == section for item in plan)
            for section in BUILDERS
        },
        "approved": sum(item["review_status"] == "approved" for item in plan),
        "draft": sum(item["review_status"] == "draft" for item in plan),
    }
    if args.apply:
        report["import"] = apply_plan(plan)
        report["index"] = verify()
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()