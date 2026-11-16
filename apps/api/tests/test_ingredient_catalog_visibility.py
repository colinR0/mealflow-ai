"""Recipe fragments stay out of the relational inventory dictionary."""

from app.db.models import Ingredient
from app.db.session import SessionLocal
from app.services import ingredient_catalog as catalog


def test_import_hidden_until_user_explicitly_adds_it():
    with SessionLocal.begin() as session:
        session.add(Ingredient(
            key="fragment", name="cm 两端葱段", category="其他", default_unit="份",
            catalog_source="recipe_import",
        ))
    with SessionLocal.begin() as session:
        assert catalog.search_ingredients(session, "cm 两端") == []
        row = catalog.resolve_ingredient(session, name="cm 两端葱段")
        assert row.catalog_source == "user"
        ingredient_id = row.id
    with SessionLocal() as session:
        assert [row.id for row in catalog.search_ingredients(session, "cm 两端")] == [ingredient_id]


def test_custom_food_persists_and_presets_are_idempotent():
    with SessionLocal.begin() as session:
        assert catalog.ensure_dictionary_presets(session) == 0
        food = catalog.resolve_ingredient(session, name="自家种的秋葵")
        assert food.catalog_source == "user"
        ingredient_id = food.id
    with SessionLocal() as session:
        assert catalog.resolve_ingredient(session, name="自家种的秋葵").id == ingredient_id
        assert catalog.search_ingredients(session, "自家种的秋葵")[0].id == ingredient_id
        assert catalog.search_ingredients(session, "番茄")
