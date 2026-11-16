"""Phase 2 retrieval routing, fixed templates and offline bundle."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal

from app.db.models import Ingredient, Recipe, RecipeIngredient, Workspace
from app.db.session import SessionLocal
from app.db.v2_models import (
    HouseholdProfile,
    InventoryLot,
    KnowledgeDocument,
    KnowledgeSource,
)
from app.services.food_constraints import HouseholdConstraint, check_recipe, household_constraints
from app.services.offline_bundle import build_bundle
from app.services.recipe_actions import llm_recipe_action, quick_action, recipe_payloads


def _constraint() -> HouseholdConstraint:
    return HouseholdConstraint("p1", "妈妈", "allergy", "peanut", "花生", ("peanut", "花生"))


def test_allergy_hit_blocks_with_fixed_wording():
    check = check_recipe(
        [_constraint()], ingredient_names=["花生酱"], ingredient_keys=["peanut_butter"]
    )
    assert check.blocked is True
    assert check.status == "matched"
    assert check.line() == "忌口校验：已对照：妈妈·过敏·花生；结论：不可推荐"


def test_no_hit_is_not_detected_and_never_says_safe():
    check = check_recipe([], ingredient_names=["鸡蛋"])
    assert check.status == "not_detected"
    assert check.blocked is False
    assert "安全" not in check.line()
    assert check.line() == "忌口校验：已对照：无已登记过敏/忌口；结论：通过"


def test_registered_constraint_without_hit_is_reported_as_not_matched():
    check = check_recipe([_constraint()], ingredient_names=["鸡蛋"])
    assert check.has_constraints is True
    assert check.status == "not_detected"
    assert check.line() == "忌口校验：已对照：未命中已登记过敏/忌口；结论：通过"


def test_restriction_label_with_bracket_note_still_matches_the_food():
    """A note inside the label used to survive as part of the term, so 花生酱 slipped through."""
    with SessionLocal() as session:
        workspace_id = _workspace(session, "括号备注忌口家")
        session.add(
            HouseholdProfile(
                workspace_id=workspace_id,
                display_name="爸爸",
                restrictions=[
                    {
                        "type": "allergy",
                        "ingredient_key": "花生（过敏）",
                        "ingredient_name": "花生（过敏）",
                    },
                    "不吃花生酱（过敏）",
                ],
            )
        )
        session.commit()
        constraints = household_constraints(session, workspace_id)
    assert [item.terms for item in constraints] == [("花生",), ("花生酱",)]
    assert check_recipe(constraints, ingredient_names=["花生酱"]).blocked is True
    assert check_recipe(constraints, ingredient_names=["花生酥"]).blocked is True
    assert check_recipe(constraints, ingredient_names=["鸡蛋"]).blocked is False


def test_long_custom_restriction_key_is_kept_as_a_term():
    """Cleaning must never lose a restriction: an over-long key stays matchable."""
    with SessionLocal() as session:
        workspace_id = _workspace(session, "自定义忌口家")
        session.add(
            HouseholdProfile(
                workspace_id=workspace_id,
                display_name="妈妈",
                restrictions=[
                    {
                        "type": "dislike",
                        "ingredient_key": "custom_0123456789abcdef0123456789abcdef",
                        "ingredient_name": "自定义忌口食材",
                    }
                ],
            )
        )
        session.commit()
        constraints = household_constraints(session, workspace_id)
    assert "custom_0123456789abcdef0123456789abcdef" in constraints[0].terms
    assert check_recipe(constraints, ingredient_names=["自定义忌口食材"]).hits


def _workspace(session, name: str) -> str:
    workspace = Workspace(name=name)
    session.add(workspace)
    session.flush()
    return workspace.id


def _recipe(session, workspace_id: str, name: str, key: str, label: str) -> str:
    ingredient = Ingredient(key=key, name=label, category="测试", default_unit="份")
    session.add(ingredient)
    session.flush()
    recipe = Recipe(
        name=name,
        servings=1,
        duration_minutes=10,
        steps=["洗净", "下锅"],
        source_name="测试来源",
    )
    session.add(recipe)
    session.flush()
    session.add(
        RecipeIngredient(
            recipe_id=recipe.id,
            ingredient_id=ingredient.id,
            quantity=Decimal("1"),
            unit="份",
            optional=False,
        )
    )
    session.flush()
    return recipe.id


def _document(session, recipe_id: str, content: dict, *, published: bool = True) -> None:
    source = session.query(KnowledgeSource).first()
    if source is None:
        source = KnowledgeSource(
            name="测试知识库", approval="approved", license_note="测试许可",
        )
        session.add(source)
        session.flush()
    session.add(KnowledgeDocument(
        recipe_id=recipe_id,
        source_id=source.id,
        title="测试文档",
        content=content,
        search_text=" ".join(str(value) for value in content.values()),
        published=published,
    ))
    session.flush()


def _lot(session, workspace_id: str, key: str, label: str) -> None:
    ingredient = session.query(Ingredient).filter_by(key=key).one()
    session.add(
        InventoryLot(
            workspace_id=workspace_id,
            ingredient_id=ingredient.id,
            ingredient_key=key,
            name=label,
            quantity_status="known",
            quantity=Decimal("2"),
            unit="份",
            confirmed_at=datetime.now(UTC),
            source_type="manual",
            source_id=f"{workspace_id}-{key}",
        )
    )
    session.commit()


def test_inventory_action_renders_steps_and_data_version():
    with SessionLocal() as session:
        workspace_id = _workspace(session, "库存家")
        recipe_id = _recipe(session, workspace_id, "清炒测试菜", "test_stock", "测试菜")
        _lot(session, workspace_id, "test_stock", "测试菜")
        result = quick_action(session, workspace_id, "inventory")
    assert result.template == "local"
    assert "【按库存推荐】清炒测试菜" in result.text
    assert "步骤：" in result.text and "1. 洗净" in result.text
    assert result.citations == [recipe_id]
    assert result.check["status"] == "not_detected"
    assert result.data_version and result.fallback_allowed is True


def test_recipe_ingredients_fall_back_to_published_document():
    with SessionLocal() as session:
        workspace_id = _workspace(session, "文档食材家")
        recipe = Recipe(
            name="文档番茄菜", servings=1, duration_minutes=8,
            steps=["切番茄"], source_name="测试来源",
        )
        session.add(recipe)
        session.flush()
        _document(session, recipe.id, {
            "category": "家常",
            "ingredients": [
                {"name": "番茄", "ingredient_key": "doc_tomato",
                 "quantity": None, "unit": None},
            ],
        })
        session.add(Ingredient(
            key="doc_tomato", name="番茄", category="蔬菜", default_unit="",
        ))
        session.flush()
        _lot(session, workspace_id, "doc_tomato", "番茄")
        session.commit()
        result = quick_action(session, workspace_id, "inventory")
    assert result.citations == [recipe.id]
    assert "番茄" in result.text
    assert result.check["status"] == "not_detected"


def test_document_ingredients_drop_bracketed_corpus_notes():
    """Corpus names reach the mobile detail page, the offline bundle and the restriction check."""
    with SessionLocal() as session:
        _workspace(session, "语料清洗家")
        recipe = Recipe(
            name="白菜芝麻菜", servings=2, duration_minutes=15,
            steps=["洗净", "炒制"], source_name="测试来源",
        )
        session.add(recipe)
        session.flush()
        _document(session, recipe.id, {
            "category": "家常",
            "ingredients": [
                {
                    "name": "大白菜（冬季黄心白菜最佳）",
                    "ingredient_key": "doc_cabbage",
                    "quantity": 1,
                    "unit": "颗",
                },
                {"name": "熟白芝麻（用于点缀）"},
                {"name": "盆"},
                "小葱、现摘新鲜",
            ],
        })
        session.commit()
        payloads = {item["id"]: item for item in recipe_payloads(session)}
    ingredients = payloads[recipe.id]["ingredients"]
    assert [item["name"] for item in ingredients] == ["大白菜", "熟白芝麻", "小葱"]
    assert ingredients[0]["key"] == "doc_cabbage"
    assert ingredients[0]["quantity"] == 1
    assert ingredients[0]["unit"] == "颗"
    assert ingredients[1]["quantity"] is None


class _FakeLLM:
    def __init__(self, payload: dict):
        self.payload = payload

    async def chat(self, *, messages):
        return json.dumps(self.payload, ensure_ascii=False)


def test_llm_fallback_checks_constraints_and_marks_recipe_unverified():
    payload = {
        "title": "虾仁蒸蛋",
        "ingredients": [
            {"name": "鸡蛋", "amount": "2个"},
            {"name": "虾仁", "amount": "150克"},
        ],
        "steps": ["鸡蛋打散", "加入虾仁蒸熟"],
    }
    result = asyncio.run(
        llm_recipe_action(
            [_constraint()], "虾仁蒸蛋", version="v-test", provider=_FakeLLM(payload),
        )
    )
    assert result is not None
    assert result.template == "web"
    assert result.fallback_allowed is False
    assert result.recipes[0]["name"] == "虾仁蒸蛋"
    assert "模型补充" in result.text
    assert "未核实" in result.text
    assert "食材：鸡蛋 2个、虾仁 150克" in result.text
    assert "未命中已登记过敏/忌口" in result.text
    assert "不能视为安全" in result.text
    assert result.check["blocked"] is False


def test_llm_fallback_blocks_generated_recipe_on_allergy():
    payload = {
        "title": "花生蒸蛋",
        "ingredients": [
            {"name": "鸡蛋", "amount": "2个"},
            {"name": "花生", "amount": "20克"},
        ],
        "steps": ["混合", "蒸熟"],
    }
    result = asyncio.run(
        llm_recipe_action(
            [_constraint()], "花生蒸蛋", version="v-test", provider=_FakeLLM(payload),
        )
    )
    assert result is not None
    assert "妈妈·过敏·花生" in result.text
    assert "不可推荐" in result.text
    assert result.check["blocked"] is True


def test_recipe_without_ingredients_is_cannot_determine():
    with SessionLocal() as session:
        workspace_id = _workspace(session, "缺食材家")
        recipe = Recipe(name="缺食材菜", servings=1, duration_minutes=5, steps=[])
        session.add(recipe)
        session.flush()
        _document(session, recipe.id, {"category": "家常", "ingredients": []})
        session.commit()
        result = quick_action(session, workspace_id, "restrictions", query="缺食材菜")
    assert result.check["status"] == "cannot_determine"
    assert result.fallback_allowed is False
    assert "未执行" in result.check["line"]
    assert "通过" not in result.check["line"]


def test_restriction_quick_action_empty_query_does_not_pick_first_recipe():
    with SessionLocal() as session:
        workspace_id = _workspace(session, "空查询忌口家")
        recipe_id = _recipe(
            session, workspace_id, "B52轰炸机", "test_b52", "B52轰炸机",
        )
        _document(session, recipe_id, {
            "category": "饮品",
            "ingredients": [{"name": "咖啡利口酒", "ingredient_key": "test_b52"}],
        })
        session.commit()
        result = quick_action(session, workspace_id, "restrictions", query="")
    assert result.template == "no_result"
    assert result.recipes == []
    assert "请先输入菜名或食材" in result.text
    assert "B52" not in result.text
    assert result.fallback_allowed is False
    assert result.check["status"] == "no_result"


def test_restriction_quick_action_only_uses_published_recipes():
    with SessionLocal() as session:
        workspace_id = _workspace(session, "发布过滤家")
        published_id = _recipe(
            session, workspace_id, "公开番茄菜", "test_published", "番茄",
        )
        hidden_id = _recipe(
            session, workspace_id, "未发布番茄菜", "test_hidden", "番茄",
        )
        _document(session, published_id, {
            "category": "家常",
            "ingredients": [{"name": "番茄", "ingredient_key": "test_published"}],
        }, published=True)
        _document(session, hidden_id, {
            "category": "家常",
            "ingredients": [{"name": "番茄", "ingredient_key": "test_hidden"}],
        }, published=False)
        session.commit()
        result = quick_action(session, workspace_id, "restrictions", query="番茄")
    assert "公开番茄菜" in result.text
    assert "未发布番茄菜" not in result.text
    assert result.citations == [published_id]


def test_skill_quick_action_empty_query_does_not_pick_first_skill():
    with SessionLocal() as session:
        workspace_id = _workspace(session, "空查询技巧家")
        result = quick_action(session, workspace_id, "skills", query="")
    assert result.template == "no_result"
    assert "请先输入烹饪问题或技巧关键词" in result.text
    assert result.skills == []


def test_blocked_inventory_keeps_allergy_line_and_switches_recipe():
    with SessionLocal() as session:
        workspace_id = _workspace(session, "过敏家")
        _recipe(session, workspace_id, "花生测试菜", "test_peanut", "花生")
        session.add(
            HouseholdProfile(
                workspace_id=workspace_id,
                display_name="妈妈",
                restrictions=[
                    {"type": "allergy", "ingredient_key": "test_peanut", "ingredient_name": "花生"}
                ],
            )
        )
        _lot(session, workspace_id, "test_peanut", "花生")
        result = quick_action(session, workspace_id, "inventory")
    assert result.check["blocked"] is True
    assert "妈妈·过敏·花生；结论：不可推荐" in result.text
    assert "不能通过替换食材规避" in result.text
    assert "更换菜谱" in result.text
    assert result.fallback_allowed is False


def test_no_stock_returns_unchecked_no_result():
    with SessionLocal() as session:
        workspace_id = _workspace(session, "空库存家")
        _recipe(session, workspace_id, "空库存菜", "test_empty", "空库存菜")
        result = quick_action(session, workspace_id, "inventory")
    assert result.template == "no_result"
    assert result.check["line"] == "忌口校验：未执行：未选出可校验的菜谱；结论：无法判断"
    assert "通过" not in result.check["line"]


def test_offline_bundle_is_versioned_and_typed():
    with SessionLocal() as session:
        workspace_id = _workspace(session, "离线家")
        _recipe(session, workspace_id, "离线测试菜", "test_offline", "离线菜")
        session.add(
            HouseholdProfile(
                workspace_id=workspace_id,
                display_name="爸爸",
                restrictions=[
                    {
                        "type": "dislike",
                        "ingredient_key": "test_offline",
                        "ingredient_name": "离线菜",
                    }
                ],
            )
        )
        session.commit()
        first = build_bundle(session, workspace_id)
        second = build_bundle(session, workspace_id)
    assert first["schema_version"] == 1
    assert first["data_version"] == second["data_version"]
    assert "离线测试菜" in {recipe["name"] for recipe in first["recipes"]}
    offline_recipe = next(
        recipe for recipe in first["recipes"] if recipe["name"] == "离线测试菜"
    )
    assert offline_recipe["search_tokens"]
    assert first["offline_rules"]["search"] == "lexical_tokens"
    assert "爸爸" in {item["member_name"] for item in first["constraints"]}
    assert first["offline_rules"]["generation"] is False
    assert "recipes_howtooknow" not in json.dumps(first, ensure_ascii=False)


def test_offline_action_marks_template_and_version():
    with SessionLocal() as session:
        workspace_id = _workspace(session, "离线动作家")
        _recipe(session, workspace_id, "离线动作菜", "test_offline_action", "离线动作菜")
        _lot(session, workspace_id, "test_offline_action", "离线动作菜")
        result = quick_action(session, workspace_id, "inventory", offline=True)
    assert result.template == "offline"
    assert "【离线·按库存推荐】" in result.text
    assert "离线数据版本：" in result.text