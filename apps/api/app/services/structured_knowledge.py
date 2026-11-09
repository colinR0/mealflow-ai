"""Isolated structured knowledge retrieval: vector recall, then typed rows.

The three libraries share an embedding profile, but not their tables or decision
fields. A vector hit is only a candidate ID/version; all substitution ratios,
flavor proportions and risk rules come from the typed row loaded by that ID.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from sqlalchemy import Connection, exists, select, text

from app.agent.tool_runtime import ToolResult
from app.core.config import Settings, get_settings
from app.db.v2_models import EmbeddingProfile, FlavorTemplate, FoodRiskRule, IngredientSubstitution
from app.services.hybrid_retrieval import cosine
from app.services.native_retrieval import tokenize_chinese, vector_literal
from app.services.retrieval_gateway import (
    QueryEmbedder,
    RetrievalUnavailable,
    configured_embedder,
    database_connection,
)

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


@dataclass(frozen=True)
class StructuredHit:
    id: str
    version: int
    score: float
    keyword_hit: bool
    distance: float | None = None


def structured_profile(connection, settings: Settings, provider: QueryEmbedder) -> dict:
    """Find the non-generation-bound embedding profile used by structured rows."""
    row = (
        connection.execute(
            select(
                EmbeddingProfile.id,
                EmbeddingProfile.model,
                EmbeddingProfile.dimensions,
                EmbeddingProfile.template_version,
                EmbeddingProfile.tokenizer_version,
                EmbeddingProfile.is_fake,
            )
            .where(
                EmbeddingProfile.model == provider.model,
                EmbeddingProfile.dimensions == provider.dimensions,
                EmbeddingProfile.template_version == provider.template_version,
                EmbeddingProfile.tokenizer_version == provider.tokenizer_version,
                EmbeddingProfile.is_fake == provider.fake,
            )
            .order_by(EmbeddingProfile.created_at.desc(), EmbeddingProfile.id)
            .limit(1)
        )
        .mappings()
        .first()
    )
    if row is None:
        raise RetrievalUnavailable("NO_EMBEDDING_PROFILE")
    expected = (
        settings.retrieval_embedding_model,
        settings.retrieval_embedding_dimensions,
        settings.retrieval_template_version,
        settings.retrieval_tokenizer_version,
    )
    indexed = (row["model"], row["dimensions"], row["template_version"],
               row["tokenizer_version"])
    if indexed != expected or row["is_fake"] != provider.fake:
        raise RetrievalUnavailable("EMBEDDING_PROFILE_MISMATCH")
    if settings.retrieval_mode == "demo" and not provider.fake:
        raise RetrievalUnavailable("DEMO_REQUIRES_FAKE_PROVIDER")
    return {
        "profile_id": row["id"],
        "model": row["model"],
        "dimensions": row["dimensions"],
        "template_version": row["template_version"],
        "tokenizer_version": row["tokenizer_version"],
        "is_fake": row["is_fake"],
    }


def search_ids_native(
    connection: Connection,
    spec: StructuredSpec,
    query: str,
    embedding: list[float],
    *,
    profile_id: str,
    limit: int,
) -> list[StructuredHit]:
    params = {
        "profile_id": profile_id,
        "query": tokenize_chinese(query),
        "vector": vector_literal(embedding),
        "channel_limit": max(limit * 8, 40),
        "limit": min(100, max(1, limit)),
    }
    # IDs and row versions are returned here; the structured row is loaded separately.
    sql = text(f"""
        WITH keyword AS (
          SELECT id, version, row_number() OVER (
            ORDER BY ts_rank(search_tsv, plainto_tsquery('simple', :query)) DESC, id
          ) rank
          FROM {spec.table}
          WHERE review_status = 'approved'
            AND embedding_profile_id = :profile_id
            AND search_tsv @@ plainto_tsquery('simple', :query)
        ), vector AS (
          SELECT id, version, embedding_vector <=> CAST(:vector AS vector) distance,
                 row_number() OVER (
                   ORDER BY embedding_vector <=> CAST(:vector AS vector), id
                 ) rank
          FROM {spec.table}
          WHERE review_status = 'approved'
            AND embedding_profile_id = :profile_id
            AND embedding_vector IS NOT NULL
        ), candidates AS (
          SELECT id, version, 'keyword'::text channel, 1.0 / (60 + rank) rrf,
                 NULL::float distance
          FROM keyword WHERE rank <= :channel_limit
          UNION ALL
          SELECT id, version, 'vector', 1.0 / (60 + rank), distance
          FROM vector WHERE rank <= :channel_limit
        )
        SELECT id, version, sum(rrf) score,
               bool_or(channel = 'keyword') keyword_hit, min(distance) distance
        FROM candidates
        GROUP BY id, version
        ORDER BY score DESC, id
        LIMIT :limit
    """)
    return [
        StructuredHit(
            id=row.id,
            version=int(row.version),
            score=float(row.score),
            keyword_hit=bool(row.keyword_hit),
            distance=float(row.distance) if row.distance is not None else None,
        )
        for row in connection.execute(sql, params).all()
    ]


def search_ids_demo(
    connection: Connection,
    spec: StructuredSpec,
    query: str,
    embedding: list[float],
    *,
    profile_id: str,
    limit: int,
) -> list[StructuredHit]:
    rows = [
        SimpleNamespace(**row)
        for row in connection.execute(
            select(spec.model).where(
                spec.model.review_status == "approved",
                spec.model.embedding_profile_id == profile_id,
            )
        )
        .mappings()
        .all()
    ]
    terms = [term for term in tokenize_chinese(query).split() if term]
    keyword: list[tuple[int, Any]] = []
    vector: list[tuple[float, Any]] = []
    for row in rows:
        rag_text = row.rag_text.lower()
        overlap = sum(term in rag_text for term in terms)
        if overlap:
            keyword.append((overlap, row))
        try:
            similarity = cosine(embedding, row.embedding)
        except (TypeError, ValueError):  # Skip rows with invalid demo vectors.
            continue
        if similarity > 0:
            vector.append((similarity, row))
    keyword.sort(key=lambda item: (-item[0], item[1].id))
    vector.sort(key=lambda item: (-item[0], item[1].id))
    scores: dict[str, float] = {}
    versions: dict[str, int] = {}
    keyword_hits: set[str] = set()
    channel_limit = max(limit * 8, 40)
    for channel, candidates in (("keyword", keyword), ("vector", vector)):
        for rank, (_, row) in enumerate(candidates[:channel_limit], 1):
            scores[row.id] = scores.get(row.id, 0.0) + 1.0 / (60 + rank)
            versions[row.id] = row.version
            if channel == "keyword":
                keyword_hits.add(row.id)
    ranked = sorted(scores, key=lambda row_id: (-scores[row_id], row_id))[:limit]
    return [
        StructuredHit(
            id=row_id,
            version=versions[row_id],
            score=scores[row_id],
            keyword_hit=row_id in keyword_hits,
        )
        for row_id in ranked
    ]


def load_structured_rows(
    connection: Connection,
    spec: StructuredSpec,
    hits: list[StructuredHit],
    *,
    profile_id: str,
) -> list[tuple[Any, float]]:
    if not hits:
        return []
    rows = [
        SimpleNamespace(**row)
        for row in connection.execute(
            select(spec.model).where(spec.model.id.in_([hit.id for hit in hits]))
        )
        .mappings()
        .all()
    ]
    by_id = {row.id: row for row in rows}
    loaded = []
    for hit in hits:
        row = by_id.get(hit.id)
        if (
            row is None
            or row.version != hit.version
            or row.review_status != "approved"
            or row.embedding_profile_id != profile_id
        ):
            raise RetrievalUnavailable("STRUCTURED_EVIDENCE_CHANGED")
        loaded.append((row, hit.score))
    return loaded


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
    settings: Settings | None = None,
    provider: QueryEmbedder | None = None,
    limit: int = 8,
) -> ToolResult:
    spec = STRUCTURED_KINDS.get(kind)
    if spec is None:
        return ToolResult(status="error", error_code="INVALID_KNOWLEDGE_KIND")
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        return ToolResult(status="error", error_code="INVALID_QUERY")
    if type(limit) is not int or not 1 <= limit <= 100:
        return ToolResult(status="error", error_code="INVALID_LIMIT")
    settings = settings or get_settings()
    provider = provider or configured_embedder(settings)
    try:
        with database_connection(sessions=sessions, connection=connection) as db:
            if settings.retrieval_mode == "native" and db.dialect.name != "postgresql":
                raise RetrievalUnavailable("NATIVE_REQUIRES_POSTGRESQL")
            with db.begin():
                profile = structured_profile(db, settings, provider)
                if settings.retrieval_mode == "native":
                    available = db.scalar(
                        text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
                    )
                    if not available:
                        raise RetrievalUnavailable("NATIVE_VECTOR_UNAVAILABLE")
                empty_library = not db.scalar(
                    select(
                        exists().where(
                            spec.model.review_status == "approved",
                            spec.model.embedding_profile_id == profile["profile_id"],
                        )
                    )
                )
            # No DB transaction spans a possibly remote/local embedding request.
            try:
                embedding = provider.embed_query(query)
            except Exception as exc:
                raise RetrievalUnavailable("EMBEDDING_PROVIDER_FAILED") from exc
            if len(embedding) != provider.dimensions:
                raise RetrievalUnavailable("EMBEDDING_DIMENSION_MISMATCH")
            vector_literal(embedding)  # Reject bool, NaN, infinity and nonnumeric values.
            with db.begin():
                current = structured_profile(db, settings, provider)
                if current != profile:
                    raise RetrievalUnavailable("EMBEDDING_PROFILE_CHANGED")
                if settings.retrieval_mode == "native":
                    hits = search_ids_native(
                        db,
                        spec,
                        query,
                        embedding,
                        profile_id=profile["profile_id"],
                        limit=limit,
                    )
                else:
                    hits = search_ids_demo(
                        db,
                        spec,
                        query,
                        embedding,
                        profile_id=profile["profile_id"],
                        limit=limit,
                    )
                if provider.fake:
                    hits = [hit for hit in hits if hit.keyword_hit]
                rows = load_structured_rows(
                    db, spec, hits, profile_id=profile["profile_id"]
                )
                if structured_profile(db, settings, provider) != profile:
                    raise RetrievalUnavailable("EMBEDDING_PROFILE_CHANGED")
                evidence = [evidence_item(spec, row, score) for row, score in rows]
            warnings = []
            if provider.fake:
                warnings.append("fake embedding 仅验证检索链路，不代表语义质量")
                if not evidence:
                    warnings.append("fake 检索只接受关键词命中候选")
            if empty_library:
                warnings.append("该库暂无已审核的结构化数据")
            return ToolResult(
                status="ok" if evidence else "no_result",
                data={
                    "evidence": evidence,
                    "kind": kind,
                    "mode": settings.retrieval_mode,
                    "fake": provider.fake,
                    "empty_library": empty_library,
                    "search_stage": "ids_then_structured_rows",
                },
                evidence_ids=[item["id"] for item in evidence],
                data_versions={
                    "profile_id": profile["profile_id"],
                    "model": provider.model,
                    "dimensions": provider.dimensions,
                    "template_version": provider.template_version,
                    "tokenizer_version": provider.tokenizer_version,
                },
                warnings=warnings,
            )
    except RetrievalUnavailable as error:  # Preserve the structured retrieval failure code.
        return ToolResult(
            status="unavailable",
            error_code=error.code,
            data={"kind": kind, "mode": settings.retrieval_mode, "fake": provider.fake},
        )
    except Exception:  # Never turn a storage failure into an empty or safe-looking result.
        logger.exception("Structured knowledge retrieval failed for %s", kind)
        return ToolResult(
            status="unavailable",
            error_code="STRUCTURED_RETRIEVAL_FAILED",
            data={"kind": kind, "mode": settings.retrieval_mode, "fake": provider.fake},
        )