"""Typed SQL lookup for the three structured food-knowledge libraries.

These tables are small, reviewed decision tables rather than retrieval corpora.
Queries read approved rows and match typed fields deterministically; there is no
embedding channel and no reciprocal-rank fusion. Unknown input returns
``no_result`` and must never be presented as "safe".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, select

from app.agent.tool_runtime import ToolResult
from app.db.v2_models import FlavorTemplate, FoodRiskRule, IngredientSubstitution
from app.services.retrieval_gateway import database_connection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StructuredSpec:
    table: str
    model: type
    kind: str
    citation_prefix: str


STRUCTURED_KINDS = {
    "substitution": StructuredSpec(
        "ingredient_substitutions", IngredientSubstitution, "substitution", "substitution"
    ),
    "flavor": StructuredSpec("flavor_templates", FlavorTemplate, "flavor_template", "flavor"),
    "risk": StructuredSpec("food_risk_rules", FoodRiskRule, "food_risk", "risk"),
}

# Fixed synonyms for reviewed applies_to keys. This is a lookup aid, not semantic
# search: the terms themselves must still match.
APPLIES_TO_TERMS = {
    "general": ("一般人群", "全家", "成人"),
    "infant": ("婴幼儿", "婴儿", "宝宝", "1岁以内", "一岁以内"),
    "child": ("儿童", "孩子", "小孩"),
    "pregnancy": ("孕期", "孕妇", "怀孕", "备孕"),
    "disease": ("慢性病", "疾病", "病人"),
    "gout": ("痛风", "高尿酸"),
    "diabetes": ("糖尿病", "血糖"),
    "hypertension": ("高血压",),
    "kidney_disease": ("肾病", "肾脏"),
}
SEVERITY_RANK = {"danger": 3, "warning": 2, "info": 1}


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _substitution_score(row: IngredientSubstitution, query: str) -> int:
    from_name = _text(row.from_ingredient_name)
    from_key = _text(row.from_ingredient_key)
    score = 0
    if from_name and from_name == query:
        score = 6
    elif from_name and from_name in query:
        score = max(score, 5)
    elif len(query) >= 2 and query in from_name:
        score = max(score, 4)
    if from_key and from_key == query:
        score = max(score, 5)
    elif from_key and from_key in query:
        score = max(score, 4)
    if _text(row.to_ingredient_name) in query:
        score = max(score, 2)
    return score


def _flavor_score(row: FlavorTemplate, query: str) -> int:
    name = _text(row.name)
    score = 0
    if name and name == query:
        score = 6
    elif name and name in query:
        score = 5
    elif len(query) >= 2 and query in name:
        score = max(score, 4)
    for raw_alias in row.aliases or []:
        alias = _text(raw_alias)
        if alias == query:
            score = max(score, 5)
        elif alias and alias in query:
            score = max(score, 4)
        elif len(query) >= 2 and alias and query in alias:
            score = max(score, 3)
    return score


def _risk_score(row: FoodRiskRule, query: str) -> int:
    title = _text(row.title)
    risk_type = _text(row.risk_type)
    score = 0
    if title and title == query:
        score = 6
    elif title and (title in query or (len(query) >= 2 and query in title)):
        score = 5
    if risk_type and (risk_type == query or risk_type in query):
        score = max(score, 4)
    for raw_key in row.applies_to or []:
        key = _text(raw_key)
        if key and (key == query or key in query):
            score = max(score, 4)
        for term in APPLIES_TO_TERMS.get(key, ()):
            if _text(term) in query:
                score = max(score, 4)
    for raw_term in [*(row.condition_tags or []), *(row.ingredient_keys or [])]:
        term = _text(raw_term)
        if term and term in query:
            score = max(score, 3)
    return score


def _rank(
    spec: StructuredSpec, rows: list[Any], query: str, limit: int
) -> list[tuple[Any, int]]:
    scored = []
    for row in rows:
        if spec.kind == "substitution":
            score = _substitution_score(row, query)
        elif spec.kind == "flavor_template":
            score = _flavor_score(row, query)
        else:
            score = _risk_score(row, query)
        if score:
            scored.append((row, score))
    if spec.kind == "substitution":
        scored.sort(key=lambda pair: (-pair[1], -(pair[0].priority or 0), pair[0].id))
    elif spec.kind == "flavor_template":
        scored.sort(key=lambda pair: (-pair[1], pair[0].name, pair[0].id))
    else:
        scored.sort(
            key=lambda pair: (-pair[1], -SEVERITY_RANK.get(pair[0].severity, 0), pair[0].id)
        )
    return scored[:limit]


def evidence_item(spec: StructuredSpec, row: Any, score: float) -> dict:
    item = {
        "id": f"{spec.citation_prefix}:{row.id}:v{row.version}",
        "kind": spec.kind,
        "version": row.version,
        "review_status": row.review_status,
        "source_name": row.source_name,
        "source_url": row.source_url,
        "notes": row.notes,
        "score": score,
    }
    if spec.kind == "substitution":
        item.update(
            title=f"{row.from_ingredient_name} → {row.to_ingredient_name}",
            content={
                "from_ingredient_key": row.from_ingredient_key,
                "from_ingredient_name": row.from_ingredient_name,
                "to_ingredient_key": row.to_ingredient_key,
                "to_ingredient_name": row.to_ingredient_name,
                "conversion_factor": str(row.conversion_factor),
                "unit": row.unit,
                "diet_tags": row.diet_tags,
                "exclude_tags": row.exclude_tags,
                "flavor_impact": row.flavor_impact,
                "priority": row.priority,
            },
        )
    elif spec.kind == "flavor_template":
        item.update(
            title=row.name,
            content={
                "name": row.name,
                "aliases": row.aliases,
                "flavor_tags": row.flavor_tags,
                "components": row.components,
                "ratio_basis": row.ratio_basis,
                "usage": row.usage,
                "cautions": row.cautions,
            },
        )
    else:
        item.update(
            title=row.title,
            content={
                "risk_type": row.risk_type,
                "severity": row.severity,
                "applies_to": row.applies_to,
                "min_age_months": row.min_age_months,
                "max_age_months": row.max_age_months,
                "condition_tags": row.condition_tags,
                "ingredient_keys": row.ingredient_keys,
                "advice": row.advice,
                "evidence_level": row.evidence_level,
            },
        )
    return item


def search_structured(
    kind: str,
    query: str,
    *,
    sessions=None,
    connection: Connection | None = None,
    limit: int = 8,
) -> ToolResult:
    spec = STRUCTURED_KINDS.get(kind)
    if spec is None:
        return ToolResult(status="error", error_code="INVALID_KNOWLEDGE_KIND")
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        return ToolResult(status="error", error_code="INVALID_QUERY")
    if type(limit) is not int or not 1 <= limit <= 100:
        return ToolResult(status="error", error_code="INVALID_LIMIT")
    normalized = query.strip().lower()
    try:
        with database_connection(sessions=sessions, connection=connection) as db:
            rows = [
                spec.model(**row._mapping)
                for row in db.execute(
                    select(spec.model).where(spec.model.review_status == "approved")
                )
            ]
            evidence = [
                evidence_item(spec, row, float(score))
                for row, score in _rank(spec, rows, normalized, limit)
            ]
        empty_library = not rows
        warnings = []
        if empty_library:
            warnings.append("该库暂无已审核的结构化数据")
        elif not evidence:
            warnings.append("没有匹配的结构化条目；这不代表饮食安全，请换用更具体的词")
        return ToolResult(
            status="ok" if evidence else "no_result",
            data={
                "evidence": evidence,
                "kind": kind,
                "empty_library": empty_library,
                "search_stage": "typed_lookup",
            },
            evidence_ids=[item["id"] for item in evidence],
            data_versions={"table": spec.table, "retrieval": "typed_sql"},
            warnings=warnings,
        )
    except Exception:
        # Never turn a storage failure into an empty or safe-looking result.
        logger.exception("Structured knowledge lookup failed for %s", kind)
        return ToolResult(
            status="unavailable",
            error_code="STRUCTURED_RETRIEVAL_FAILED",
            data={"kind": kind},
        )