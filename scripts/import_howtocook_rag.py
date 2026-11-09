"""Import whole-recipe documents; default is a read-only preview.

Run from the repository root with the mealflow interpreter. DATABASE_URL must
target the dedicated mealflow_rag database for --apply. No quantities are inferred.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from hashlib import sha256
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.core.config import get_settings
from app.db.models import Recipe
from app.db.schema_guard import ensure_schema_current
from app.db.session import SessionLocal, engine
from app.db.v2_models import (
    EmbeddingProfile,
    KnowledgeDocument,
    KnowledgeSource,
)
from app.services.native_index_worker import rebuild_generation
from app.services.native_retrieval import install_native_schema
from app.services.recipe_taxonomy import IngredientFact, classify_recipe
from app.services.retrieval_gateway import (
    configured_embedder,
    document_embedder,
    search,
)
from import_lazy_cookbook import (
    DURATION_BY_DIFFICULTY,
    _allergens,
    _clean_stuff,
    _normalize_ingredient,
    _steps,
    _tags,
    _taste_tags,
)
from sqlalchemy import select, text

SOURCE_NAME = "HowToCook 清洗菜谱（本地试跑）"
LICENSE = "用户提供的清洗数据，仅用于本地试跑；原始授权待核实"


def profile_id(settings, provider) -> str:
    if provider.fake:
        return "howtocook-fake-64-v1"
    identity = "|".join(
        [
            provider.model,
            str(provider.dimensions),
            provider.template_version,
            provider.tokenizer_version,
        ]
    )
    return "emb-" + sha256(identity.encode("utf-8")).hexdigest()[:32]


SETTINGS = get_settings()
PROVIDER = configured_embedder(SETTINGS)
EMBED = document_embedder(PROVIDER)
DIMENSIONS = PROVIDER.dimensions
PROFILE = profile_id(SETTINGS, PROVIDER)


def stable_id(key: str) -> str:
    return str(uuid5(NAMESPACE_URL, "mealflow:howtocook-rag:v1:" + key))


def build_plan(source: Path) -> tuple[list[dict], dict]:
    entries = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise TypeError("Source must be a JSON array")
    plan, seen, stats = [], set(), Counter(input=len(entries))
    name_counts = Counter(str(e.get("name") or "").strip() for e in entries)
    for index, entry in enumerate(entries, 1):
        name = str(entry.get("name") or "").strip()
        steps = _steps(entry)
        names = _clean_stuff(entry.get("stuff"))
        if not name or len(name) > 120 or not steps or not names:
            raise ValueError(f"Invalid name/ingredients/steps at record {index}")
        # Identical names can have different source variants; retain those variants.
        identity = json.dumps([name, names, steps], ensure_ascii=False)
        if identity in seen:
            stats["exact_duplicates_skipped"] += 1
            continue
        seen.add(identity)
        pairs, facts, ingredients = [], [], []
        for original in names:
            key, canonical, category, _unit = _normalize_ingredient(original)
            pairs.append((original, key))
            facts.append(IngredientFact(key, canonical, category, None, ""))
            ingredients.append({"name": original, "ingredient_key": key,
                                "quantity": None, "unit": None})
        tags = _tags(entry)
        classified = classify_recipe(name, tags, facts)
        # Names remain stable across content edits; variants use a content-derived ID.
        key = name if name_counts[name] == 1 else identity
        heat = entry.get("heat")
        if heat is not None and (type(heat) is not int or heat < 0):
            raise ValueError(f"Invalid heat at record {index}")
        values = {
            "name": name, "servings": 2,
            "duration_minutes": DURATION_BY_DIFFICULTY.get(entry.get("difficulty"), 30),
            "kcal_per_serving": heat, "steps": steps, "tags": tags,
            "allergens": _allergens(pairs), "taste_tags": _taste_tags(entry, pairs),
            "meal_role": classified.meal_role or "其他",
            "staple_type": classified.staple_type,
            "protein_sources": classified.protein_sources,
            "vegetable_keys": classified.vegetable_keys,
            "portion_confidence": "servings_only",
            "source_name": SOURCE_NAME, "source_url": None, "license_note": LICENSE,
        }
        content = {**values, "ingredients": ingredients,
                   "category": entry.get("category") or "其他",
                   "tools": entry.get("tools") or "",
                   "difficulty": entry.get("difficulty") or "其他",
                   "defaults": {"servings": 2, "duration_minutes": "difficulty heuristic"}}
        plan.append({"key": key, "recipe": values, "content": content,
                     "search_text": " ".join([name, *names, *steps])})
        stats["ingredients"] += len(names)
        stats["default_role"] += values["meal_role"] == "其他"
    stats["documents"] = len(plan)
    stats["duplicate_name_variants"] = len(plan) - len({p["recipe"]["name"] for p in plan})
    return plan, dict(stats)


def prepare_import() -> None:
    settings = get_settings()
    if engine.url.database != "mealflow_rag" or settings.app_env != "development":
        raise RuntimeError("--apply requires APP_ENV=development and database mealflow_rag")
    if settings.retrieval_mode != "native":
        raise RuntimeError("--apply requires RETRIEVAL_MODE=native")
    ensure_schema_current()
    with engine.begin() as connection:
        # install_native_schema rejects an old vector dimension before indexing.
        install_native_schema(connection, dimensions=DIMENSIONS)


def apply_plan(plan: list[dict]) -> dict:
    prepare_import()
    counts = Counter(created=0, updated=0)
    with SessionLocal.begin() as session:
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext('howtocook-rag-import'))"))
        if session.get(KnowledgeSource, stable_id("source")) is None:
            session.add(KnowledgeSource(id=stable_id("source"), name=SOURCE_NAME,
                                       approval="approved", license_note=LICENSE, url=None))
        identity = (PROVIDER.model, PROVIDER.dimensions, PROVIDER.template_version,
                    PROVIDER.tokenizer_version, PROVIDER.fake)
        profile = session.get(EmbeddingProfile, PROFILE)
        if profile is None:
            session.add(EmbeddingProfile(
                id=PROFILE, model=PROVIDER.model, dimensions=PROVIDER.dimensions,
                template_version=PROVIDER.template_version,
                tokenizer_version=PROVIDER.tokenizer_version, is_fake=PROVIDER.fake,
            ))
        elif (profile.model, profile.dimensions, profile.template_version,
              profile.tokenizer_version, profile.is_fake) != identity:
            raise RuntimeError("Existing embedding profile differs from configured provider")
        session.flush()
        for item in plan:
            recipe_id = stable_id("recipe:" + item["key"])
            recipe = session.get(Recipe, recipe_id)
            if recipe is None:
                recipe = Recipe(id=recipe_id, **item["recipe"])
                session.add(recipe)
                counts["created"] += 1
            else:
                for field, value in item["recipe"].items():
                    setattr(recipe, field, value)
            session.flush()
            doc = session.get(KnowledgeDocument, stable_id("document:" + item["key"]))
            if doc is None:
                session.add(KnowledgeDocument(id=stable_id("document:" + item["key"]),
                    recipe_id=recipe_id, source_id=stable_id("source"), published=True,
                    content_version=1, capabilities={"ingredients": True, "steps": True,
                                                   "quantities": False},
                    content=item["content"], search_text=item["search_text"]))
            elif doc.content != item["content"] or doc.search_text != item["search_text"]:
                doc.content, doc.search_text = item["content"], item["search_text"]
                doc.content_version += 1
                counts["updated"] += 1
    return {**counts, "generation_id": ensure_generation()}


def ensure_generation() -> str:
    with engine.connect() as connection:
        generation = connection.scalar(text("""
            SELECT g.id FROM index_generations g WHERE g.active AND g.status='ready'
            AND g.profile_id=:profile AND NOT EXISTS (
              SELECT 1 FROM knowledge_documents d WHERE NOT EXISTS (
                SELECT 1 FROM knowledge_chunks c WHERE c.document_id=d.id
                AND c.content_version=d.content_version AND c.generation_id=g.id
                AND c.embedding_vector IS NOT NULL AND c.search_tsv IS NOT NULL))
        """), {"profile": PROFILE})
        connection.rollback()
        if generation is None:
            generation = rebuild_generation(
                connection,
                profile_id=PROFILE,
                embed=EMBED,
                fake=PROVIDER.fake,
                model=PROVIDER.model,
            )
    return generation


def verify(query: str) -> dict:
    from app.agent.durable_graph import QueryInput, Runtime

    result = search(query, sessions=SessionLocal)
    # Exercise the same tool method used by the persistent Agent; no remote LLM needed.
    agent = asyncio.run(Runtime(SessionLocal, None).search_recipes(QueryInput(query=query)))
    for outcome in (result, agent):
        if outcome.status != "ok" or not all(
            e["content"].get("steps") for e in outcome.data["evidence"]
        ):
            raise RuntimeError(f"Retrieval/tool verification failed: {outcome.error_code}")
    with SessionLocal() as session:
        source_docs = session.scalars(select(KnowledgeDocument).where(
            KnowledgeDocument.source_id == stable_id("source"))).all()
        for doc in source_docs:
            recipe = session.get(Recipe, doc.recipe_id)
            if doc.content["steps"] != recipe.steps or any(
                i["quantity"] is not None or i["unit"] is not None
                for i in doc.content["ingredients"]
            ):
                raise RuntimeError("Stored steps/unknown quantities differ from import contract")
        index_counts = session.execute(text("""
            SELECT count(*) AS chunks,
              count(DISTINCT c.document_id) AS documents,
              count(*) FILTER (WHERE c.ordinal=0 AND c.embedding_vector IS NOT NULL
                AND vector_dims(c.embedding_vector)=:dimensions AND c.search_tsv IS NOT NULL
                AND c.text=d.search_text AND c.content_version=d.content_version) AS valid
            FROM knowledge_chunks c JOIN index_generations g ON g.id=c.generation_id
            JOIN knowledge_documents d ON d.id=c.document_id
            WHERE g.active AND d.source_id=:source
        """), {"source": stable_id("source"), "dimensions": DIMENSIONS}).mappings().one()
        if not (index_counts["chunks"] == index_counts["documents"]
                == index_counts["valid"] == len(source_docs)):
            raise RuntimeError(
                f"Expected one complete {DIMENSIONS}-dimensional chunk per document"
            )
    return {"query": query, "gateway": result.status, "agent_tool": agent.status,
            "index": dict(index_counts),
            "fake": result.data["fake"], "mode": result.data["mode"],
            "model": PROVIDER.model, "dimensions": PROVIDER.dimensions,
            "hits": [{"name": e["content"]["name"], "steps": e["content"]["steps"],
                      "evidence_id": e["id"]} for e in agent.data["evidence"]]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--query", default="番茄")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    plan, stats = build_plan(args.source)
    report = {"preview": stats}
    if args.apply:
        report["import"] = apply_plan(plan)
        report["verification"] = verify(args.query)
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
