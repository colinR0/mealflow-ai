import json

import pytest

from app.agent.intent import generate_recipe, parse_meal_intent
from tests.test_conversation_agent import _context


class ChatQueueProvider:
    def __init__(self, values: list[str]):
        self.values = values

    async def chat(self, *, messages):
        return self.values.pop(0)


@pytest.mark.asyncio
async def test_structured_intent_preserves_generic_positive_and_negative_dishes() -> None:
    provider = ChatQueueProvider(
        [
            json.dumps(
                {
                    "task_type": "meal_plan",
                    "diners": 2,
                    "budget_limit_cents": 6000,
                    "requested_dishes": ["任意测试主菜"],
                    "excluded_dishes": ["任意测试凉菜"],
                    "preferred_ingredients": [],
                    "excluded_ingredients": [],
                    "style": "",
                    "preference_updates": [],
                },
                ensure_ascii=False,
            )
        ]
    )
    intent = await parse_meal_intent(provider, "测试输入", [])
    assert intent is not None
    assert intent.requested_dishes == ["任意测试主菜"]
    assert intent.excluded_dishes == ["任意测试凉菜"]
    assert intent.budget_limit_cents == 6000


@pytest.mark.asyncio
async def test_generated_recipe_is_ephemeral_and_marked_with_source() -> None:
    provider = ChatQueueProvider(
        [
            json.dumps(
                {
                    "name": "任意库外菜",
                    "servings": 2,
                    "duration_minutes": 25,
                    "ingredients": [
                        {"name": "番茄", "quantity": 2, "unit": "个", "optional": False},
                        {"name": "新食材", "quantity": 100, "unit": "g", "optional": False},
                    ],
                    "steps": ["准备食材", "加热至熟"],
                    "meal_role": "mixed_dish",
                    "staple_type": "无",
                    "protein_sources": [],
                    "taste_tags": ["家常"],
                    "allergens": [],
                },
                ensure_ascii=False,
            )
        ]
    )
    recipe = await generate_recipe(provider, "任意库外菜", 2, _context())
    assert recipe is not None
    assert recipe.source == "model_generated"
    assert recipe.estimated_cost_cents is None
    assert recipe.ingredients[0].key == "tomato"
    assert recipe.ingredients[1].key.startswith("generated:")


@pytest.mark.asyncio
async def test_invalid_generated_recipe_retries_then_declines_structured_result() -> None:
    provider = ChatQueueProvider(["not-json", '{"name":"missing fields"}'])
    assert await generate_recipe(provider, "未知菜", 2, _context()) is None


def _generated_payload(ingredients: list[dict]) -> str:
    return json.dumps(
        {
            "name": "任意库外菜",
            "servings": 2,
            "duration_minutes": 25,
            "ingredients": ingredients,
            "steps": ["准备食材", "加热至熟"],
            "meal_role": "mixed_dish",
            "staple_type": "无",
            "protein_sources": [],
            "taste_tags": ["家常"],
            "allergens": [],
        },
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_generated_recipe_names_drop_descriptors_before_procurement() -> None:
    """These names feed the missing-ingredient procurement draft, so notes must not survive."""
    provider = ChatQueueProvider(
        [
            _generated_payload(
                [
                    {"name": "番茄（现摘新鲜）", "quantity": 2, "unit": "个"},
                    {"name": "荔浦芋头、实惠新鲜）", "quantity": 300, "unit": "g"},
                    {"name": "盆", "quantity": 1, "unit": "个"},
                ]
            )
        ]
    )
    recipe = await generate_recipe(provider, "任意库外菜", 2, _context())
    assert recipe is not None
    assert [line.name for line in recipe.ingredients] == ["番茄", "荔浦芋头"]
    assert recipe.ingredients[0].key == "tomato"
    assert recipe.ingredients[0].quantity == 2
    assert recipe.ingredients[1].key.startswith("generated:")
    assert recipe.ingredients[1].quantity == 300


@pytest.mark.asyncio
async def test_generated_recipe_with_only_note_names_is_declined() -> None:
    provider = ChatQueueProvider(
        [
            _generated_payload(
                [
                    {"name": "盆", "quantity": 1, "unit": "个"},
                    {"name": "适量", "quantity": 1, "unit": "份"},
                ]
            )
        ]
    )
    assert await generate_recipe(provider, "任意库外菜", 2, _context()) is None
