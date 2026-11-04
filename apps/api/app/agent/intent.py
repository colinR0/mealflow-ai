"""模型驱动的餐饮意图与临时菜谱结构化解析。"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.llm.provider import LLMProvider
from app.services.context_loader import PlanningContext, RecipeContext, RecipeIngredientLine


class PreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: Literal["dish", "ingredient"]
    target_name: str = Field(min_length=1, max_length=160)
    preference: Literal["like", "dislike"]
    explicit: bool = True


class MealIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: Literal["general", "recipe", "meal_plan", "preference_update"] = "general"
    diners: int | None = Field(default=None, ge=1, le=12)
    budget_limit_cents: int | None = Field(default=None, ge=1)
    requested_dishes: list[str] = Field(default_factory=list)
    excluded_dishes: list[str] = Field(default_factory=list)
    preferred_ingredients: list[str] = Field(default_factory=list)
    excluded_ingredients: list[str] = Field(default_factory=list)
    style: str = ""
    preference_updates: list[PreferenceUpdate] = Field(default_factory=list)


class GeneratedIngredient(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    quantity: Decimal = Field(gt=0)
    unit: str = Field(min_length=1, max_length=20)
    optional: bool = False


class GeneratedRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    servings: int = Field(ge=1, le=12)
    duration_minutes: int = Field(ge=1, le=360)
    ingredients: list[GeneratedIngredient] = Field(min_length=1, max_length=40)
    steps: list[str] = Field(min_length=2, max_length=30)
    meal_role: Literal[
        "protein_dish", "vegetable_dish", "mixed_dish", "soup", "staple_side", "complete_meal"
    ]
    staple_type: Literal["无", "米饭", "面食", "杂粮"] = "无"
    protein_sources: list[str] = Field(default_factory=list)
    taste_tags: list[str] = Field(default_factory=list)
    allergens: list[str] = Field(default_factory=list)


_INTENT_PROMPT = """你是 MealFlow 的结构化意图解析器。只输出一个 JSON 对象，不要解释。
字段：task_type(general|recipe|meal_plan|preference_update)、diners、budget_limit_cents、
requested_dishes、excluded_dishes、preferred_ingredients、excluded_ingredients、style、preference_updates。
用户明确想吃或指定的菜放 requested_dishes；明确不喜欢/不要某道菜放 excluded_dishes，并在
preference_updates 中写 {target_type:"dish",target_name,preference:"dislike",explicit:true}。
不喜欢某种食材则 target_type 为 ingredient。不要把普通询问推断成长期偏好。
金额换算为分；没有的信息使用 null、空字符串或空数组。"""


def _json_object(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.I)
    match = re.search(r"\{.*\}", cleaned, re.S)
    if not match:
        raise ValueError("missing JSON object")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("intent must be an object")
    return value


async def parse_meal_intent(
    provider: LLMProvider,
    user_text: str,
    history: list[dict[str, str]],
) -> MealIntent | None:
    messages: list[dict] = [
        {"role": "system", "content": _INTENT_PROMPT},
        *history[-6:],
        {"role": "user", "content": user_text},
    ]
    for attempt in range(2):
        try:
            raw = await provider.chat(messages=messages)
            return MealIntent.model_validate(_json_object(raw))
        except Exception:  # noqa: BLE001 - provider/schema failure must degrade safely
            if attempt == 0:
                messages.append(
                    {
                        "role": "system",
                        "content": "上一份输出未通过 schema 校验。请仅重新输出完整合法 JSON。",
                    }
                )
    return None


def _recipe_match(recipes: list[RecipeContext], name: str) -> RecipeContext | None:
    normalized = "".join(name.lower().split())
    exact = next(
        (item for item in recipes if "".join(item.name.lower().split()) == normalized), None
    )
    if exact:
        return exact
    candidates = [
        item
        for item in recipes
        if normalized in "".join(item.name.lower().split())
        or "".join(item.name.lower().split()) in normalized
    ]
    return min(candidates, key=lambda item: len(item.name), default=None)


def find_recipe(context: PlanningContext, name: str) -> RecipeContext | None:
    return _recipe_match(context.recipes, name)


def normalize_preference_key(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).strip().lower().split())


def upsert_member_preferences(
    workspace_id: str,
    context: PlanningContext,
    updates: list[PreferenceUpdate],
) -> list[dict[str, str]]:
    explicit = [item for item in updates if item.explicit]
    if not explicit:
        return []
    # This legacy route has no authenticated actor; never infer whose preferences to write.
    return [{
        "status": "skipped",
        "message": "请在家庭助手中确认个人偏好；本轮条件仍用于菜单建议",
    }]


async def generate_recipe(
    provider: LLMProvider,
    dish_name: str | None,
    diners: int,
    context: PlanningContext,
    *,
    requirements: dict[str, Any] | None = None,
) -> RecipeContext | None:
    requirements = requirements or {}
    target_role = str(requirements.get("meal_role") or "")
    guidance = {
        "meal_role": target_role or None,
        "style": str(requirements.get("style") or ""),
        "excluded_ingredients": requirements.get("excluded_ingredients") or [],
        "forbidden_ingredients": requirements.get("forbidden_ingredients") or [],
        "available_ingredients": requirements.get("available_ingredients") or [],
        "pantry_staples": requirements.get("pantry_staples") or [],
    }
    target = f"目标菜：{dish_name}。" if dish_name else "请自行命名一道人名清晰的菜。"
    prompt = (
        "你是家庭菜谱结构化生成器。只输出 JSON，字段为 name、servings、duration_minutes、"
        "ingredients[{name,quantity,unit,optional}]、steps、meal_role、staple_type、"
        "protein_sources、taste_tags、allergens。用量必须为正数；步骤至少两条；"
        "meal_role 只能是 protein_dish、vegetable_dish、mixed_dish、soup、"
        "staple_side、complete_meal。"
        f"{target}人数：{diners}。"
        "优先使用 available_ingredients 和 pantry_staples；允许其他主料，但不得使用 "
        "forbidden_ingredients 或 excluded_ingredients。严格遵守下列生成约束："
        + json.dumps(guidance, ensure_ascii=False)
    )
    messages: list[dict] = [{"role": "system", "content": prompt}]
    parsed: GeneratedRecipe | None = None
    for attempt in range(2):
        try:
            raw = await provider.chat(messages=messages)
            parsed = GeneratedRecipe.model_validate(_json_object(raw))
            if dish_name:
                requested_name = normalize_preference_key(dish_name)
                generated_name = normalize_preference_key(parsed.name)
                if not (
                    requested_name == generated_name
                    or requested_name in generated_name
                    or generated_name in requested_name
                ):
                    raise ValueError("generated recipe name does not match request")
            if parsed.servings != diners:
                raise ValueError("generated recipe servings do not match request")
            if target_role and parsed.meal_role != target_role:
                raise ValueError("generated recipe role does not match menu gap")
            break
        except Exception:  # noqa: BLE001 - provider/schema failure must degrade safely
            if attempt == 0:
                messages.append(
                    {
                        "role": "system",
                        "content": "输出未通过校验。请仅输出字段完整、用量有效的 JSON。",
                    }
                )
    if parsed is None:
        return None

    known: dict[str, tuple[str, str]] = {}
    for recipe in context.recipes:
        for line in recipe.ingredients:
            known[line.name.strip().lower()] = (line.key, line.name)
    lines: list[RecipeIngredientLine] = []
    for ingredient in parsed.ingredients:
        match = known.get(ingredient.name.strip().lower())
        if match:
            key, canonical_name = match
        else:
            digest = hashlib.sha1(ingredient.name.encode("utf-8")).hexdigest()[:12]
            key, canonical_name = f"generated:{digest}", ingredient.name
        lines.append(
            RecipeIngredientLine(
                key=key,
                name=canonical_name,
                quantity=ingredient.quantity,
                unit=ingredient.unit,
                optional=ingredient.optional,
            )
        )
    recipe_id = "generated:" + hashlib.sha1(parsed.name.encode("utf-8")).hexdigest()[:16]
    return RecipeContext(
        id=recipe_id,
        name=parsed.name,
        servings=parsed.servings,
        duration_minutes=parsed.duration_minutes,
        kcal_per_serving=None,
        estimated_cost_cents=None,
        tags=[],
        taste_tags=parsed.taste_tags,
        allergens=parsed.allergens,
        steps=parsed.steps,
        ingredients=lines,
        meal_role=parsed.meal_role,
        staple_type=parsed.staple_type,
        protein_sources=parsed.protein_sources,
        portion_confidence="complete",
        source="model_generated",
    )
