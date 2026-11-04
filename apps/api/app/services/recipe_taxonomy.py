"""从菜名、标签和食材回填可审计的菜谱角色。分类不完整的菜仍可检索，但不进自动整餐。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

MEAL_ROLES = {
    "protein_dish",
    "vegetable_dish",
    "mixed_dish",
    "soup",
    "staple_side",
    "complete_meal",
}
STAPLE_TYPES = {"米饭", "面食", "杂粮", "无"}
PORTION_LEVELS = {"complete", "partial", "servings_only"}

_AROMATIC_KEYS = {"garlic", "ginger", "scallion", "cilantro", "dried_chili"}
_VAGUE = ("适量", "少许", "若干", "酌情", "一点")
_PURE_RICE = {"米饭", "白米饭", "大锅米饭", "蒸米饭"}
_COMPLETE_HINTS = (
    "汤面",
    "焖面",
    "拌面",
    "炒面",
    "烩面",
    "炒饭",
    "盖饭",
    "煲仔饭",
    "蛋炒饭",
    "盖浇饭",
)
_OVERRIDES = {
    "家常汤面": ("complete_meal", "面食"),
    "大锅米饭": ("staple_side", "米饭"),
    "黄焖鸡米饭": ("complete_meal", "米饭"),
    "洋葱炒蛋": ("mixed_dish", "无"),
    "清炒时蔬": ("vegetable_dish", "无"),
    "豆角焖面": ("complete_meal", "面食"),
    "牛奶馒头": ("staple_side", "面食"),
}
_PROTEIN_ORDER = ["猪肉", "鸡肉", "牛肉", "鱼虾", "蛋", "豆制品"]


@dataclass(frozen=True)
class IngredientFact:
    key: str
    name: str
    category: str
    quantity: Decimal | None
    unit: str
    optional: bool = False


@dataclass(frozen=True)
class RecipeClassification:
    meal_role: str
    staple_type: str
    protein_sources: list[str]
    vegetable_keys: list[str]
    portion_confidence: str

    @property
    def composable(self) -> bool:
        return is_composable(self.meal_role, self.staple_type)


def is_composable(meal_role: str, staple_type: str) -> bool:
    if meal_role in {"protein_dish", "vegetable_dish", "mixed_dish", "soup"}:
        return staple_type == "无"
    if meal_role in {"staple_side", "complete_meal"}:
        return staple_type in {"米饭", "面食", "杂粮"}
    return False


def classify_recipe(
    name: str, tags: list[str] | None, ingredients: list[IngredientFact]
) -> RecipeClassification:
    proteins = _proteins(ingredients)
    vegetables = _vegetables(ingredients)
    role, staple = _role_and_staple(name, list(tags or []), ingredients, proteins, vegetables)
    if staple not in STAPLE_TYPES:
        staple = "无"
    if role not in MEAL_ROLES:
        role = ""
    return RecipeClassification(
        meal_role=role,
        staple_type=staple,
        protein_sources=proteins,
        vegetable_keys=vegetables,
        portion_confidence=_portion_confidence(ingredients),
    )


def backfill_recipe_classifications(connection) -> None:
    from sqlalchemy import text

    recipes = connection.execute(text("SELECT id, name, tags FROM recipes")).mappings().all()
    links = connection.execute(
        text(
            """
            SELECT ri.recipe_id, i.key, i.name, i.category, ri.quantity, ri.unit, ri.optional
            FROM recipe_ingredients ri
            JOIN ingredients i ON i.id = ri.ingredient_id
            """
        )
    ).mappings().all()
    grouped: dict[str, list[IngredientFact]] = {}
    for link in links:
        quantity = None if link["quantity"] is None else Decimal(str(link["quantity"]))
        grouped.setdefault(link["recipe_id"], []).append(
            IngredientFact(
                key=link["key"],
                name=link["name"],
                category=link["category"] or "",
                quantity=quantity,
                unit=link["unit"] or "",
                optional=bool(link["optional"]),
            )
        )
    statement = text(
        """
        UPDATE recipes
        SET meal_role = :meal_role,
            staple_type = :staple_type,
            protein_sources = :protein_sources,
            vegetable_keys = :vegetable_keys,
            portion_confidence = :portion_confidence
        WHERE id = :id
        """
    )
    for recipe in recipes:
        classified = classify_recipe(
            recipe["name"], _json_list(recipe["tags"]), grouped.get(recipe["id"], [])
        )
        connection.execute(
            statement,
            {
                "id": recipe["id"],
                "meal_role": classified.meal_role,
                "staple_type": classified.staple_type,
                "protein_sources": json.dumps(classified.protein_sources, ensure_ascii=False),
                "vegetable_keys": json.dumps(classified.vegetable_keys, ensure_ascii=False),
                "portion_confidence": classified.portion_confidence,
            },
        )


def _json_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value:
        try:
            data = json.loads(value)
        except json.JSONDecodeError:  # Invalid imported JSON contributes no classification tags.
            return []
        if isinstance(data, list):
            return [str(item) for item in data]
    return []


def _role_and_staple(
    name: str,
    tags: list[str],
    ingredients: list[IngredientFact],
    proteins: list[str],
    vegetables: list[str],
) -> tuple[str, str]:
    if name in _OVERRIDES:
        return _OVERRIDES[name]
    staple = _infer_staple_type(name, ingredients)
    if any(hint in name for hint in _COMPLETE_HINTS) or ("米饭" in name and name not in _PURE_RICE):
        return "complete_meal", staple if staple != "无" else "米饭"
    if "汤" in name and "面" not in name and "饭" not in name:
        return "soup", "无"
    staple_only = name in _PURE_RICE or any(token in name for token in ("馒头", "花卷", "粥"))
    tagged_staple = "主食" in tags and not proteins and not vegetables
    bare_staple = not proteins and not vegetables and _has_staple_ingredient(ingredients)
    if staple_only or tagged_staple or bare_staple:
        return "staple_side", staple if staple != "无" else "米饭"
    if proteins and vegetables:
        return "mixed_dish", "无"
    if proteins:
        return "protein_dish", "无"
    if vegetables:
        return "vegetable_dish", "无"
    return "", "无"


def _infer_staple_type(name: str, ingredients: list[IngredientFact]) -> str:
    if any(token in name for token in ("杂粮", "玉米", "红薯", "燕麦", "紫米")):
        return "杂粮"
    if "饭" in name or "米" in name:
        return "米饭"
    if any(token in name for token in ("面", "馒头", "花卷", "饼", "包子")):
        return "面食"
    keys = {item.key for item in ingredients}
    if "rice" in keys and not (keys & {"noodles", "flour"}):
        return "米饭"
    if keys & {"noodles", "flour"}:
        return "面食"
    return "无"


def _proteins(ingredients: list[IngredientFact]) -> list[str]:
    found: list[str] = []
    for item in ingredients:
        if item.optional:
            continue
        label = _protein_label(item.key, item.name, item.category)
        if label and label not in found:
            found.append(label)
    return sorted(found, key=_PROTEIN_ORDER.index)


def _protein_label(key: str, name: str, category: str) -> str | None:
    if key == "milk" or ("奶" in name and "蛋" not in name):
        return None
    if key == "seaweed" or category == "水产干货":
        return None
    if key.startswith("chicken") or "鸡" in name:
        return "鸡肉"
    if key == "beef" or "牛" in name:
        return "牛肉"
    if key in {"fish", "shrimp"} or any(token in name for token in ("鱼", "虾")):
        return "鱼虾"
    if key == "pork" or any(token in name for token in ("猪", "肉丝", "肉丸", "排骨", "五花")):
        return "猪肉"
    if key == "egg" or "蛋" in name:
        return "蛋"
    if key == "tofu" or "豆腐" in name or category == "豆制品":
        return "豆制品"
    if category == "肉禽":
        return "猪肉"
    if category == "水产":
        return "鱼虾"
    return None


def _vegetables(ingredients: list[IngredientFact]) -> list[str]:
    keys: list[str] = []
    for item in ingredients:
        if item.optional or item.key in _AROMATIC_KEYS or item.category != "蔬菜":
            continue
        if item.key not in keys:
            keys.append(item.key)
    return keys


def _has_staple_ingredient(ingredients: list[IngredientFact]) -> bool:
    return any(
        item.category == "主食" or item.key in {"rice", "noodles", "flour"} for item in ingredients
    )


def _portion_confidence(ingredients: list[IngredientFact]) -> str:
    required = [item for item in ingredients if not item.optional]
    if not required:
        return "servings_only"
    numeric = sum(1 for item in required if not _vague(item))
    if numeric == len(required):
        return "complete"
    if numeric == 0:
        return "servings_only"
    return "partial"


def _vague(item: IngredientFact) -> bool:
    if item.quantity is None:
        return True
    unit = item.unit or ""
    return not unit.strip() or any(token in unit for token in _VAGUE)
