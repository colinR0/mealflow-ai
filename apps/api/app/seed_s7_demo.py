"""Explicit, synthetic S7 development data; never run automatically at API startup."""

import argparse
import getpass
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from argon2 import PasswordHasher
from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.models import Ingredient, Recipe, RecipeIngredient, User, Workspace, WorkspaceMember
from app.db.schema_guard import ensure_schema_current
from app.db.session import SessionLocal, engine
from app.db.v2_models import (
    EmbeddingProfile,
    HouseholdProfile,
    InventoryLot,
    KnowledgeDocument,
    KnowledgeSource,
)
from app.services.hybrid_retrieval import fake_embedding
from app.services.knowledge_demo import PROFILE
from app.services.native_index_worker import rebuild_generation
from app.services.native_retrieval import NativeRetriever, install_native_schema

PHONE = "13900000007"  # Synthetic local demo credential; no SMS is sent.
LICENSE = "项目自造演示数据，可再分发；无客户或第三方菜谱数据"
INGREDIENTS = {
    "tomato": "番茄", "egg": "鸡蛋", "tofu": "豆腐", "broccoli": "西兰花",
    "rice": "大米", "peanut": "花生", "shrimp": "虾",
}
RECIPES = [
    ("tomato-egg", "番茄炒蛋", ["tomato", "egg"], ["鸡蛋"]),
    ("tomato-tofu", "番茄炖豆腐", ["tomato", "tofu"], ["大豆"]),
    ("broccoli-tofu", "西兰花炒豆腐", ["broccoli", "tofu"], ["大豆"]),
    ("rice", "清蒸米饭", ["rice"], []),
    ("peanut-shrimp", "花生炒虾仁", ["peanut", "shrimp"], ["花生", "虾"]),
]


def demo_id(key: str) -> str:
    return str(uuid5(NAMESPACE_URL, "mealflow:s7-demo:v1:" + key))


def seed(credential_file: Path | None) -> dict:
    settings = get_settings()
    if settings.app_env != "development":
        raise SystemExit("seed_s7_demo requires APP_ENV=development")
    if engine.dialect.name != "postgresql" or engine.url.database != "mealflow_s7":
        raise SystemExit("seed_s7_demo requires the dedicated PostgreSQL mealflow_s7 database")
    ensure_schema_current()
    with engine.connect() as connection:
        version = connection.scalar(text("SELECT current_setting('server_version_num')::int"))
        if version // 10000 != 16:
            raise SystemExit("This demo requires PostgreSQL 16 with pgvector")
        connection.rollback()
        with connection.begin():
            install_native_schema(connection, dimensions=64)

    created_credentials = False
    with SessionLocal() as session, session.begin():
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext('mealflow:s7-demo:seed'))"))
        user = session.get(User, demo_id("user"))
        if user is None:
            password = secrets.token_urlsafe(24) if credential_file else getpass.getpass(
                "New S7 demo password (12+ characters): "
            )
            if len(password) < 12:
                raise SystemExit("Password must contain at least 12 characters")
            if credential_file:
                credential_file.parent.mkdir(parents=True, exist_ok=True)
                # Exclusive creation prevents overwriting another demo's credentials.
                with credential_file.open("x", encoding="utf-8") as stream:
                    json.dump({"phone": PHONE, "password": password}, stream)
                created_credentials = True
            user = User(id=demo_id("user"), phone=PHONE, display_name="向量演示用户",
                        password_hash=PasswordHasher().hash(password))
            session.add(user)
            session.flush()
        user.phone = PHONE
        workspace_id = demo_id("household")
        if session.get(Workspace, workspace_id) is None:
            session.add(Workspace(id=workspace_id, name="S7 独立向量演示家庭", kind="personal"))
            session.flush()
        if session.get(WorkspaceMember, demo_id("membership")) is None:
            session.add(WorkspaceMember(id=demo_id("membership"), workspace_id=workspace_id,
                                        user_id=user.id, role="user", status="active"))
        if session.get(HouseholdProfile, demo_id("profile")) is None:
            session.add(HouseholdProfile(
                id=demo_id("profile"), workspace_id=workspace_id, display_name="演示常住成员",
                linked_user_id=user.id, restrictions=["花生", "虾"], preferences=[],
                legacy_details={"provenance": "synthetic-s7-demo"},
            ))
        for key, name in INGREDIENTS.items():
            ingredient = session.scalar(select(Ingredient).where(Ingredient.key == key))
            if ingredient is None:
                ingredient = Ingredient(id=demo_id("ingredient:" + key), key=key, name=name,
                                        category="演示食材", default_unit="g")
                session.add(ingredient)
                session.flush()
            if key in ("tomato", "egg", "tofu", "rice"):
                lot_id = demo_id("lot:" + key)
                if session.get(InventoryLot, lot_id) is None:
                    session.add(InventoryLot(
                        id=lot_id, workspace_id=workspace_id, ingredient_key=key, name=name,
                        quantity_status="known", quantity=500, unit="g", expires_on=None,
                        confirmed_at=datetime.now(UTC), source_type="s7_demo",
                        source_id=key, version=1,
                    ))
        source_id = demo_id("source")
        if session.get(KnowledgeSource, source_id) is None:
            session.add(KnowledgeSource(id=source_id, name="S7 自造已批准演示来源",
                                        approval="approved", license_note=LICENSE, url=None))
        if session.get(EmbeddingProfile, PROFILE) is None:
            session.add(EmbeddingProfile(id=PROFILE, model="fake", dimensions=64,
                                        template_version="s7-demo-v1", tokenizer_version="jieba-v1",
                                        is_fake=True))
        session.flush()
        for key, name, ingredients, allergens in RECIPES:
            recipe_id = demo_id("recipe:" + key)
            steps = ["清洗并准备食材。", "按食材特点充分加热至熟，分装上桌。"]
            if session.get(Recipe, recipe_id) is None:
                session.add(Recipe(
                    id=recipe_id, name=name, servings=2, duration_minutes=20, tags=["演示", "家常"],
                    taste_tags=["清淡"], allergens=allergens, steps=steps,
                    source_name="S7 自造演示", source_url=None, license_note=LICENSE,
                ))
                session.flush()
                for ingredient_key in ingredients:
                    ingredient = session.scalar(select(Ingredient).where(
                        Ingredient.key == ingredient_key))
                    session.add(RecipeIngredient(
                        id=demo_id(f"recipe-ingredient:{key}:{ingredient_key}"),
                        recipe_id=recipe_id, ingredient_id=ingredient.id, quantity=200, unit="g",
                    ))
            document_id = demo_id("document:" + key)
            if session.get(KnowledgeDocument, document_id) is None:
                content = {
                    "name": name, "servings": 2, "duration_minutes": 20,
                    "ingredients": [{"ingredient_key": item, "name": INGREDIENTS[item],
                                     "quantity": 200, "unit": "g"} for item in ingredients],
                    "steps": steps, "allergens": allergens, "tags": ["清淡", "演示"],
                    "source_name": "S7 自造演示", "license_note": LICENSE,
                }
                session.add(KnowledgeDocument(
                    id=document_id, recipe_id=recipe_id, source_id=source_id, content_version=1,
                    published=True, capabilities={"ingredients": True, "steps": True},
                    content=content, search_text=name + " " + " ".join(
                        INGREDIENTS[item] for item in ingredients) + " " + " ".join(steps),
                ))

    with engine.connect() as connection:
        generation_id = connection.scalar(text("""
            SELECT g.id FROM index_generations g
            WHERE g.active AND g.status = 'ready' AND g.profile_id = :profile
            AND NOT EXISTS (
                SELECT 1 FROM knowledge_documents d WHERE NOT EXISTS (
                    SELECT 1 FROM knowledge_chunks c WHERE c.document_id = d.id
                    AND c.content_version = d.content_version AND c.generation_id = g.id
                    AND c.embedding_vector IS NOT NULL AND c.search_tsv IS NOT NULL
                )
            )
        """), {"profile": PROFILE})
        # The builder owns transactions; never pass an autobegun connection.
        connection.rollback()
        built = generation_id is None
        if built:
            generation_id = rebuild_generation(connection, profile_id=PROFILE)
        hits = NativeRetriever(connection, profile_id=PROFILE).search(
            "番茄豆腐", fake_embedding("番茄豆腐"))
        if not hits:
            raise RuntimeError("Native retrieval verification returned no evidence")
    return {"phone": PHONE, "household_id": workspace_id, "generation_id": generation_id,
            "native_build": "built" if built else "reused", "native_hits": len(hits),
            "fake_embedding": True, "credentials_created": created_credentials}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credential-file", type=Path,
                        help="Write generated NEW-account credentials exclusively to a local file")
    args = parser.parse_args()
    if args.credential_file:
        repo = Path(__file__).resolve().parents[3]
        if args.credential_file.resolve().is_relative_to(repo):
            raise SystemExit("Credential file must be outside the repository")
    print(json.dumps(seed(args.credential_file), ensure_ascii=False))


if __name__ == "__main__":
    main()
