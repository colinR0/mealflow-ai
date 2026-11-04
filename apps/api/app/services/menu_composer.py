"""确定性整餐组合。模型只能解释结果，不能改写这里选出的菜。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from itertools import combinations
from typing import Any

from app.services.context_loader import PlanningContext, RecipeContext
from app.services.planning_rules import scale_quantity
from app.services.recipe_taxonomy import is_composable

RAW_RICE_GRAMS = 80
COOKED_RICE_GRAMS = 190
DRY_NOODLE_GRAMS = 100
PORTION_NOTE = "份量为标准成人估算，可按饭量调整"
_ANIMAL = {"猪肉", "鸡肉", "鱼虾", "牛肉"}
_VAGUE = ("适量", "少许", "若干", "酌情", "一点")
_ROLE_LABEL = {
    "protein_dish": "荤菜",
    "vegetable_dish": "素菜",
    "mixed_dish": "荤素",
    "soup": "汤",
    "staple_side": "主食",
    "complete_meal": "主食",
}
_METHODS = (
    "清炒",
    "红烧",
    "清蒸",
    "白灼",
    "醋溜",
    "宫保",
    "麻婆",
    "干煸",
    "黄焖",
    "焖",
    "烧",
    "蒸",
    "炖",
    "拌",
    "炸",
    "烤",
    "煎",
    "煮",
    "炒",
)


@dataclass
class MealSlots:
    protein: int = 0
    vegetable: int = 0
    flexible: int = 0
    soup: int = 0
    want_staple: bool = True
    staple_kind: str = "米饭"
    complete_meal: bool = False


def compose_menu(
    args: dict[str, Any], context: PlanningContext, diner_ids: list[str] | None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    # 这是菜单的确定性边界：这里计算出的菜品结构、约束过滤和采购缺口
    # 是可信结果；模型只能把它解释成自然语言，不能自行替换或添加菜。
    diners = _diners(args, context, diner_ids)
    dish_count = _optional_int(args.get("dish_count"))
    style = str(args.get("style") or "")
    need_soup = bool(args.get("need_soup")) or "一汤" in style or "带汤" in style
    if dish_count is None and "四菜一汤" in style:
        dish_count = 4
        need_soup = True
    vegetarian = bool(args.get("vegetarian")) or any(token in style for token in ("素食", "全素"))
    all_meat = bool(args.get("all_meat")) or "全肉" in style
    if vegetarian:
        all_meat = False
    staple_kind, complete_meal = _staple_request(str(args.get("staple") or ""))
    slots = _slots(diners, dish_count, need_soup, vegetarian, all_meat, staple_kind, complete_meal)
    preferred = {
        str(item).strip().lower()
        for item in args.get("preferred_ingredients") or []
        if str(item).strip()
    }
    required_names = _normalized_names(args.get("required_dishes"))
    excluded_names = _normalized_names(args.get("excluded_dishes"))
    excluded_ingredients = _normalized_names(args.get("excluded_ingredients"))
    selected_user_ids = set(diner_ids or [member.user_id for member in context.members])
    disliked_dishes = {
        _normalize_name(item.target_name)
        for item in context.food_preferences
        if item.preference == "dislike"
        and item.target_type == "dish"
        and (not selected_user_ids or item.user_id in selected_user_ids)
    }
    availability = {
        recipe.id: _availability(recipe, context, diner_ids) for recipe in context.recipes
    }
    required_recipes: list[RecipeContext] = []
    missing_required: list[str] = []
    blocked_required: list[str] = []
    warnings_by_recipe: dict[str, list[str]] = {}
    for requested in required_names:
        recipe = _find_named_recipe(context.recipes, requested)
        if recipe is None:
            missing_required.append(requested)
            continue
        groups = availability[recipe.id]
        if groups["forbidden_ingredients"]:
            blocked_required.append(recipe.name)
            continue
        required_recipes.append(recipe)
        warnings = _warning_reasons(recipe, groups, context, diner_ids)
        if _normalize_name(recipe.name) in disliked_dishes:
            warnings.append("这道菜在长期不喜欢列表中，本次因明确点名而保留")
        if warnings:
            warnings_by_recipe[recipe.id] = warnings

    required_ids = {recipe.id for recipe in required_recipes}
    eligible = [
        recipe
        for recipe in context.recipes
        if recipe.id not in required_ids
        and _normalize_name(recipe.name) not in excluded_names
        and _normalize_name(recipe.name) not in disliked_dishes
        and not _recipe_has_excluded_ingredient(recipe, excluded_ingredients)
        and _eligible(recipe, availability[recipe.id])
        and not _warning_reasons(recipe, availability[recipe.id], context, diner_ids)
    ]
    scores = {
        recipe.id: _recipe_score(recipe, availability[recipe.id], context, preferred)
        for recipe in eligible
    }
    remaining_slots = _remaining_slots(slots, required_recipes)
    staple_required = next(
        (recipe for recipe in required_recipes if recipe.meal_role in {"staple_side", "complete_meal"}),
        None,
    )
    staple_recipe = staple_required or _pick_staple(eligible, scores, remaining_slots)
    chosen, relaxations = _choose_dishes(eligible, scores, remaining_slots)
    chosen = [*(_labeled_recipe(recipe) for recipe in required_recipes if recipe is not staple_required), *chosen]
    items: list[dict[str, Any]] = []
    suggestions: dict[str, dict[str, Any]] = {}
    for recipe, label in chosen:
        view, missing = _item(
            recipe,
            label,
            diners,
            availability[recipe.id],
            warnings_by_recipe.get(recipe.id, []),
        )
        items.append(view)
        for suggestion in missing:
            key = f"{suggestion['source_id']}:{suggestion['ingredient']}"
            suggestions.setdefault(key, suggestion)
    staple = _staple_payload(staple_recipe, slots, diners, availability)
    structure = _structure_text(slots, _staple_label(staple_recipe, slots))
    actual_veg = sum(1 for _recipe, label in chosen if label == "素菜")
    actual_protein = sum(1 for _recipe, label in chosen if label == "荤菜")
    if slots.protein and slots.vegetable and actual_veg < slots.vegetable:
        relaxations.append("当前本地菜谱中暂时无法组成“一荤一素”，下面是最接近的方案，还缺少一项蔬菜。")
    elif slots.protein and actual_protein < slots.protein:
        relaxations.append("当前本地菜谱中没有足够的荤菜，下面是最接近的方案，还缺少一项肉类。")
    if any(item["role_label"] == "素菜" and item["state"] == "purchase_optional" for item in items):
        relaxations.append("缺少的蔬菜已标为可选采购，不会当成已有库存。")
    if any(item["role_label"] == "荤菜" and item["state"] == "purchase_optional" for item in items):
        relaxations.append("缺少的肉类已标为可选采购，不会当成已有库存。")
    durations = [item["duration_minutes"] for item in items]
    if staple_recipe is not None:
        durations.append(staple_recipe.duration_minutes)
    budget_recipes = [recipe for recipe, _label in chosen]
    if staple_recipe is not None and staple_recipe not in budget_recipes:
        budget_recipes.append(staple_recipe)
    estimated_costs = [
        recipe.estimated_cost_cents * diners
        for recipe in budget_recipes
        if recipe.estimated_cost_cents is not None
    ]
    budget_limit = _optional_int(args.get("budget_limit_cents"))
    budget_complete = bool(budget_recipes) and all(
        recipe.estimated_cost_cents is not None for recipe in budget_recipes
    )
    estimated_total = sum(estimated_costs)
    budget_status = "not_requested"
    if budget_limit is not None:
        if not budget_complete:
            budget_status = "partial"
        elif estimated_total > budget_limit:
            budget_status = "over"
        else:
            budget_status = "verified"
    result = {
        "diners": diners,
        "dish_count": sum(1 for item in items if item["role"] != "soup"),
        "meal_structure": structure,
        "staple": staple,
        "items": items,
        "balance_summary": structure if not relaxations else relaxations[0],
        "portion_note": PORTION_NOTE,
        "relaxations": relaxations,
        "ready_count": sum(item["state"] == "ready" for item in items),
        "purchase_optional_count": sum(item["state"] == "purchase_optional" for item in items),
        "restricted_count": len(blocked_required),
        "estimated_parallel_minutes": max(durations, default=0),
        "reason": "按人数、库存和荤素槽位生成，模型不能自行换菜",
        "style": style,
        "constraint_snapshot": {
            "required_dishes": [recipe.name for recipe in required_recipes],
            "missing_required_dishes": missing_required,
            "blocked_required_dishes": blocked_required,
            "excluded_dishes": sorted(excluded_names | disliked_dishes),
            "excluded_ingredients": sorted(excluded_ingredients),
            "budget_limit_cents": budget_limit,
        },
        "estimated_cost_cents": estimated_total if budget_complete else None,
        "budget_status": budget_status,
    }
    return result, list(suggestions.values())


def unfilled_menu_slots(
    args: dict[str, Any],
    context: PlanningContext,
    diner_ids: list[str] | None,
    menu: dict[str, Any],
) -> list[str]:
    """返回当前本地候选池实际未填满的菜单槽位。

    缺口必须以已编排的菜单为准，而不是只看“库里是否存在某道菜”：
    被过敏等硬约束拒绝的菜即使存在，也不能算作填满槽位，才可触发受控生成补齐。
    """
    diners = _diners(args, context, diner_ids)
    dish_count = _optional_int(args.get("dish_count"))
    style = str(args.get("style") or "")
    need_soup = bool(args.get("need_soup")) or "一汤" in style or "带汤" in style
    if dish_count is None and "四菜一汤" in style:
        dish_count = 4
        need_soup = True
    vegetarian = bool(args.get("vegetarian")) or any(token in style for token in ("素食", "全素"))
    all_meat = (bool(args.get("all_meat")) or "全肉" in style) and not vegetarian
    staple_kind, complete_meal = _staple_request(str(args.get("staple") or ""))
    slots = _slots(
        diners, dish_count, need_soup, vegetarian, all_meat, staple_kind, complete_meal
    )
    items = list(menu.get("items") or [])
    labels = [str(item.get("role_label") or "") for item in items]
    gaps: list[str] = []
    gaps.extend(["protein_dish"] * max(0, slots.protein - labels.count("荤菜")))
    gaps.extend(["vegetable_dish"] * max(0, slots.vegetable - labels.count("素菜")))
    gaps.extend(["soup"] * max(0, slots.soup - labels.count("汤")))
    non_soup_count = sum(1 for item in items if item.get("role") != "soup")
    gaps.extend(["mixed_dish"] * max(0, slots.flexible - non_soup_count))
    return gaps


def is_recipe_safe_for_menu(
    recipe: RecipeContext,
    context: PlanningContext,
    diner_ids: list[str] | None,
    excluded_ingredients: list[str] | None = None,
) -> bool:
    """校验模型生成菜，只有安全且可组合时才能进入本轮菜单候选池。"""
    availability = _availability(recipe, context, diner_ids)
    if not _eligible(recipe, availability):
        return False
    excluded = {
        _normalize_name(str(item)) for item in excluded_ingredients or [] if str(item).strip()
    }
    candidate_terms = {
        _normalize_name(value)
        for value in [
            *(line.key for line in recipe.ingredients),
            *(line.name for line in recipe.ingredients),
            *recipe.allergens,
        ]
    }
    known_names: dict[str, set[str]] = {
        key: {_normalize_name(name)} for key, name in context.ingredient_names.items()
    }
    for known_recipe in context.recipes:
        for line in known_recipe.ingredients:
            known_names.setdefault(line.key, set()).add(_normalize_name(line.name))
    forbidden_aliases = {
        _normalize_name(alias)
        for constraint in context.member_constraints
        if constraint.level == "forbidden"
        and constraint.ingredient_key
        and (not diner_ids or not constraint.user_id or constraint.user_id in diner_ids)
        for alias in [
            constraint.ingredient_key,
            *known_names.get(constraint.ingredient_key, set()),
        ]
    }
    blocked = excluded | forbidden_aliases
    return not any(
        candidate
        and blocked_term
        and (
            candidate == blocked_term
            or candidate in blocked_term
            or blocked_term in candidate
        )
        for candidate in candidate_terms
        for blocked_term in blocked
    )


def render_menu_reply(menu: dict[str, Any]) -> str:
    lines = [f"按 {menu['diners']} 人准备：{menu['meal_structure']}。"]
    for note in menu.get("relaxations") or []:
        lines.append(note if str(note).endswith("。") else f"{note}。")
    for item in menu.get("items") or []:
        bits = "、".join(
            f"{row['name']} {row['display']}" for row in item.get("scaled_ingredients") or []
        )
        missing = item.get("missing_main_ingredients") or []
        suffix = f"，可选采购{'、'.join(missing)}" if missing else ""
        detail = f"（{bits}）" if bits else ""
        lines.append(f"{item.get('role_label') or '菜'}：{item['name']}{detail}{suffix}。")
    staple = menu.get("staple") or {}
    if staple:
        parts = [staple.get("suggested_quantity"), staple.get("cooked_quantity")]
        detail = "，".join(part for part in parts if part)
        name = staple.get("name") or staple.get("type")
        lines.append(f"主食：{name}{('，' + detail) if detail else ''}。")
    lines.append(str(menu.get("portion_note") or PORTION_NOTE))
    return "\n".join(lines)


def _diners(args: dict[str, Any], context: PlanningContext, diner_ids: list[str] | None) -> int:
    parsed = _optional_int(args.get("diners"))
    if parsed is not None:
        return max(1, min(parsed, 12))
    if diner_ids:
        return max(1, min(len(diner_ids), 12))
    return max(1, min(int(context.expected_diners or 2), 12))


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):  # Invalid model-supplied numbers use the caller's default.
        return None


def _normalize_name(value: object) -> str:
    return "".join(str(value or "").strip().lower().split())


def _normalized_names(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {_normalize_name(item) for item in value if _normalize_name(item)}


def _find_named_recipe(recipes: list[RecipeContext], requested: str) -> RecipeContext | None:
    exact = next((item for item in recipes if _normalize_name(item.name) == requested), None)
    if exact is not None:
        return exact
    matches = [
        item
        for item in recipes
        if requested in _normalize_name(item.name) or _normalize_name(item.name) in requested
    ]
    return min(matches, key=lambda item: len(item.name), default=None)


def _recipe_has_excluded_ingredient(recipe: RecipeContext, excluded: set[str]) -> bool:
    if not excluded:
        return False
    values = {
        _normalize_name(value)
        for line in recipe.ingredients
        for value in (line.key, line.name)
    }
    return bool(values & excluded)


def _warning_reasons(
    recipe: RecipeContext,
    availability: dict[str, list[dict[str, Any]]],
    context: PlanningContext,
    diner_ids: list[str] | None,
) -> list[str]:
    reasons = [f"普通忌口：{row['name']}" for row in availability["warning_ingredients"]]
    selected = set(diner_ids or [])
    for constraint in context.member_constraints:
        if constraint.kind != "event" or (selected and constraint.user_id not in selected):
            continue
        conflicts = set(recipe.taste_tags) & set(constraint.avoid_tags)
        if conflicts:
            reasons.append(f"{constraint.summary}，冲突口味：{'、'.join(sorted(conflicts))}")
    return reasons


def _remaining_slots(slots: MealSlots, required: list[RecipeContext]) -> MealSlots:
    result = replace(slots)
    for recipe in required:
        if recipe.meal_role == "protein_dish":
            result.protein = max(0, result.protein - 1)
        elif recipe.meal_role == "vegetable_dish":
            result.vegetable = max(0, result.vegetable - 1)
        elif recipe.meal_role == "soup":
            result.soup = max(0, result.soup - 1)
        elif recipe.meal_role == "mixed_dish":
            if result.flexible:
                result.flexible -= 1
            elif result.protein:
                result.protein -= 1
            elif result.vegetable:
                result.vegetable -= 1
        elif recipe.meal_role == "complete_meal":
            result.protein = result.vegetable = result.flexible = result.soup = 0
            result.complete_meal = True
    return result


def _labeled_recipe(recipe: RecipeContext) -> tuple[RecipeContext, str]:
    return recipe, _ROLE_LABEL.get(recipe.meal_role, "主菜")


def _staple_request(text: str) -> tuple[str, bool]:
    if any(token in text for token in ("不要主食", "不用主食", "无主食")):
        return "无", False
    if any(token in text for token in ("汤面", "焖面", "炒面", "拌面", "烩面")):
        return "面食", True
    if any(token in text for token in ("炒饭", "盖饭", "煲仔饭")):
        return "米饭", True
    if "面" in text or "馒头" in text:
        return "面食", False
    if "杂粮" in text:
        return "杂粮", False
    return "米饭", False


def _default_dish_count(diners: int) -> int:
    if diners <= 1:
        return 1
    if diners == 2:
        return 2
    if diners <= 4:
        return 3
    if diners <= 6:
        return 4
    extra = (diners - 6 + 2) // 3
    return 4 + extra


def _split_balance(count: int) -> tuple[int, int]:
    protein = (count + 1) // 2
    return protein, count - protein


def _slots(
    diners: int,
    dish_count: int | None,
    need_soup: bool,
    vegetarian: bool,
    all_meat: bool,
    staple_kind: str,
    complete_meal: bool,
) -> MealSlots:
    base = _slots_for_count(diners, dish_count, need_soup, vegetarian, all_meat)
    base.staple_kind = staple_kind
    base.complete_meal = complete_meal
    base.want_staple = staple_kind != "无"
    return base


def _slots_for_count(
    diners: int, dish_count: int | None, need_soup: bool, vegetarian: bool, all_meat: bool
) -> MealSlots:
    if dish_count is None and not vegetarian and not all_meat:
        slots = _template(diners)
        if need_soup:
            slots.soup = 1
        return slots
    count = dish_count if dish_count is not None else _default_dish_count(diners)
    count = max(1, min(count, 8))
    soup = 1 if need_soup or (dish_count is None and diners >= 5) else 0
    if vegetarian:
        return MealSlots(vegetable=count, soup=soup)
    if all_meat:
        return MealSlots(protein=count, soup=soup)
    if count == 1 and diners <= 1:
        return MealSlots(flexible=1, soup=soup)
    protein, vegetable = _split_balance(count)
    return MealSlots(protein=protein, vegetable=vegetable, soup=soup)


def _template(diners: int) -> MealSlots:
    if diners <= 1:
        return MealSlots(flexible=1)
    if diners == 2:
        return MealSlots(protein=1, vegetable=1)
    if diners <= 4:
        return MealSlots(protein=2, vegetable=1)
    protein, vegetable, soup = 2, 2, 1
    if diners > 6:
        for index in range((diners - 6 + 2) // 3):
            if index % 2 == 0:
                protein += 1
            else:
                vegetable += 1
    return MealSlots(protein=protein, vegetable=vegetable, soup=soup)


def _availability(
    recipe: RecipeContext, context: PlanningContext, diner_ids: list[str] | None
) -> dict[str, list[dict[str, Any]]]:
    active_constraints = [
        item
        for item in context.member_constraints
        if not diner_ids or not item.user_id or item.user_id in diner_ids
    ]
    forbidden = {
        item.ingredient_key
        for item in active_constraints
        if item.level == "forbidden" and item.ingredient_key
    }
    warned = {
        item.ingredient_key
        for item in active_constraints
        if item.level != "forbidden" and item.ingredient_key
    }
    groups: dict[str, list[dict[str, Any]]] = {
        "available_main_ingredients": [],
        "missing_main_ingredients": [],
        "pantry_staples": [],
        "optional_ingredients": [],
        "restricted_ingredients": [],
        "forbidden_ingredients": [],
        "warning_ingredients": [],
    }
    for line in recipe.ingredients:
        row = {
            "key": line.key,
            "name": line.name,
            "quantity": str(line.quantity),
            "unit": line.unit,
        }
        if line.key in forbidden:
            groups["restricted_ingredients"].append(row)
            groups["forbidden_ingredients"].append(row)
        elif line.key in warned:
            groups["warning_ingredients"].append(row)
        elif line.optional:
            groups["optional_ingredients"].append(row)
        elif line.key in context.pantry_keys:
            groups["pantry_staples"].append(row)
        elif context.available_for(line.key) > 0:
            groups["available_main_ingredients"].append(row)
        else:
            groups["missing_main_ingredients"].append(row)
    ingredient_names: dict[str, set[str]] = {}
    for known_recipe in context.recipes:
        for line in known_recipe.ingredients:
            ingredient_names.setdefault(line.key, set()).add(_normalize_name(line.name))
    allergen_names = {_normalize_name(value) for value in recipe.allergens}
    for constraint in active_constraints:
        if not constraint.ingredient_key:
            continue
        aliases = {
            _normalize_name(constraint.ingredient_key),
            *ingredient_names.get(constraint.ingredient_key, set()),
        }
        matched = next(
            (
                allergen
                for allergen in allergen_names
                if any(alias and (alias in allergen or allergen in alias) for alias in aliases)
            ),
            None,
        )
        if matched is None:
            continue
        target = (
            groups["forbidden_ingredients"]
            if constraint.level == "forbidden"
            else groups["warning_ingredients"]
        )
        if not any(row["key"] == constraint.ingredient_key for row in target):
            row = {
                "key": constraint.ingredient_key,
                "name": next(iter(ingredient_names.get(constraint.ingredient_key, {matched}))),
                "quantity": "",
                "unit": "",
            }
            target.append(row)
            if constraint.level == "forbidden":
                groups["restricted_ingredients"].append(row)
    return groups


def _eligible(recipe: RecipeContext, availability: dict[str, list[dict[str, Any]]]) -> bool:
    if availability["forbidden_ingredients"]:
        return False
    return is_composable(recipe.meal_role, recipe.staple_type or "无")


def _recipe_score(
    recipe: RecipeContext,
    availability: dict[str, list[dict[str, Any]]],
    context: PlanningContext,
    preferred: set[str],
) -> float:
    have = availability["available_main_ingredients"]
    missing = availability["missing_main_ingredients"]
    total = len(have) + len(missing)
    coverage = len(have) / total if total else 1
    expiring_keys = context.expiring_keys(context.service_date)
    expiring = sum(
        1
        for line in recipe.ingredients
        if line.key in expiring_keys and context.available_for(line.key) > 0
    )
    feedback = context.dish_feedback.get(recipe.name)
    rating = float(feedback.avg_rating) if feedback else 0
    pref = sum(
        1
        for line in recipe.ingredients
        if line.name.lower() in preferred or line.key.lower() in preferred
    )
    cost = recipe.estimated_cost_cents or 0
    return (
        coverage * 100
        + expiring * 8
        + rating * 3
        + pref * 5
        - len(missing) * 12
        - cost / 1000
        - recipe.duration_minutes / 100
    )


def _pick_staple(
    recipes: list[RecipeContext], scores: dict[str, float], slots: MealSlots
) -> RecipeContext | None:
    if not slots.want_staple:
        return None
    role = "complete_meal" if slots.complete_meal else "staple_side"
    matches = [
        recipe
        for recipe in recipes
        if recipe.meal_role == role and recipe.staple_type == slots.staple_kind
    ]
    if not matches:
        return None
    return max(matches, key=lambda recipe: scores[recipe.id])


def _choose_dishes(
    recipes: list[RecipeContext], scores: dict[str, float], slots: MealSlots
) -> tuple[list[tuple[RecipeContext, str]], list[str]]:
    relaxations: list[str] = []
    protein_pool = _ranked(
        [recipe for recipe in recipes if recipe.meal_role == "protein_dish"], scores
    )
    if len(protein_pool) < slots.protein:
        mixed = [
            recipe
            for recipe in recipes
            if recipe.meal_role == "mixed_dish" and _ANIMAL.intersection(recipe.protein_sources)
        ]
        protein_pool = protein_pool + _ranked(mixed, scores)
    vegetable_pool = _ranked(
        [recipe for recipe in recipes if recipe.meal_role == "vegetable_dish"], scores
    )
    soup_pool = _ranked([recipe for recipe in recipes if recipe.meal_role == "soup"], scores)
    flex_pool = _ranked(
        [recipe for recipe in recipes if recipe.meal_role == "mixed_dish"], scores
    ) + _ranked([recipe for recipe in recipes if recipe.meal_role == "protein_dish"], scores)
    best: tuple[float, list[tuple[RecipeContext, str]]] | None = None
    for proteins in _choices(protein_pool, slots.protein):
        for vegetables in _choices(vegetable_pool, slots.vegetable):
            for soups in _choices(soup_pool, slots.soup):
                for flex in _choices(flex_pool, slots.flexible):
                    grouped = [
                        *[(recipe, "荤菜") for recipe in proteins],
                        *[(recipe, "素菜") for recipe in vegetables],
                        *[(recipe, "汤") for recipe in soups],
                        *[(recipe, _ROLE_LABEL.get(recipe.meal_role, "主菜")) for recipe in flex],
                    ]
                    if not _unique(grouped):
                        continue
                    score = _combo_score(grouped, scores, slots)
                    if best is None or score > best[0]:
                        best = (score, grouped)
    chosen = best[1] if best else []
    mixed_as_protein = any(
        recipe.meal_role == "mixed_dish" and label == "荤菜" for recipe, label in chosen
    )
    if slots.protein and mixed_as_protein:
        relaxations.append("纯荤菜数量不足，已用荤素混合菜补上荤菜。")
    return chosen, relaxations


def _ranked(recipes: list[RecipeContext], scores: dict[str, float]) -> list[RecipeContext]:
    return sorted(recipes, key=lambda recipe: scores[recipe.id], reverse=True)


def _choices(pool: list[RecipeContext], count: int) -> list[tuple[RecipeContext, ...]]:
    if count <= 0:
        return [()]
    narrowed = pool[:6]
    if not narrowed:
        return [()]
    if len(narrowed) < count:
        return [tuple(narrowed)]
    return list(combinations(narrowed, count))


def _unique(grouped: list[tuple[RecipeContext, str]]) -> bool:
    seen: set[str] = set()
    for recipe, _label in grouped:
        if recipe.id in seen:
            return False
        seen.add(recipe.id)
    return True


def _combo_score(
    grouped: list[tuple[RecipeContext, str]], scores: dict[str, float], slots: MealSlots
) -> float:
    chosen = [recipe for recipe, _label in grouped]
    score = sum(scores[recipe.id] for recipe in chosen)
    if slots.flexible:
        score += sum(5 for recipe in chosen if recipe.meal_role == "mixed_dish")
    proteins = [recipe.protein_sources[0] for recipe in chosen if recipe.protein_sources]
    methods = [method for method in (_cooking_method(recipe.name) for recipe in chosen) if method]
    if len(proteins) != len(set(proteins)):
        score -= 15
    if len(methods) != len(set(methods)):
        score -= 8
    return score


def _cooking_method(name: str) -> str:
    for token in _METHODS:
        if token in name:
            return token
    return ""


def _item(
    recipe: RecipeContext,
    label: str,
    diners: int,
    availability: dict[str, list[dict[str, Any]]],
    warnings: list[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    missing = availability["missing_main_ingredients"]
    state = "purchase_optional" if missing else "ready"
    suggestions = [
        {
            "ingredient": row["name"],
            "quantity": row["quantity"],
            "unit": row["unit"],
            "recipe_id": recipe.id,
            "recipe_name": recipe.name,
            "source_id": f"chat:{recipe.id}",
            "reason": f"制作{recipe.name}缺少的主料",
        }
        for row in missing
    ]
    return {
        "recipe_id": recipe.id,
        "name": recipe.name,
        "role": recipe.meal_role,
        "role_label": label,
        "target_servings": diners,
        "duration_minutes": recipe.duration_minutes,
        "state": state,
        "scaled_ingredients": _scaled_lines(recipe, diners, None),
        "used_inventory": [row["name"] for row in availability["available_main_ingredients"]],
        "pantry_staples": [row["name"] for row in availability["pantry_staples"]],
        "missing_main_ingredients": [row["name"] for row in missing],
        "optional_purchases": [row["name"] for row in missing],
        "restricted_ingredients": [],
        "source": recipe.source,
        "warnings": warnings or [],
    }, suggestions


def _staple_payload(
    recipe: RecipeContext | None,
    slots: MealSlots,
    diners: int,
    availability: dict[str, dict[str, list[dict[str, Any]]]],
) -> dict[str, Any] | None:
    if not slots.want_staple:
        return None
    suggested, cooked = _staple_quantity(recipe, slots.staple_kind, diners)
    payload: dict[str, Any] = {
        "type": slots.staple_kind,
        "name": _staple_display_name(recipe, slots),
        "suggested_quantity": suggested,
        "scaled_ingredients": _staple_lines(recipe, diners, slots.staple_kind),
    }
    if cooked:
        payload["cooked_quantity"] = cooked
    if recipe is not None:
        payload["recipe_id"] = recipe.id
        if not slots.complete_meal:
            payload["name"] = _plain_staple_name(slots.staple_kind)
        groups = availability[recipe.id]
        payload["state"] = "purchase_optional" if groups["missing_main_ingredients"] else "ready"
    return payload


def _staple_display_name(recipe: RecipeContext | None, slots: MealSlots) -> str:
    if recipe is not None and slots.complete_meal:
        return recipe.name
    return _plain_staple_name(slots.staple_kind)


def _staple_lines(
    recipe: RecipeContext | None, diners: int, staple_kind: str
) -> list[dict[str, str]]:
    if recipe is None:
        return []
    return _scaled_lines(recipe, diners, staple_kind)


def _plain_staple_name(kind: str) -> str:
    return {"米饭": "米饭", "面食": "面食", "杂粮": "杂粮"}.get(kind, kind)


def _staple_label(recipe: RecipeContext | None, slots: MealSlots) -> str:
    if not slots.want_staple:
        return ""
    if slots.complete_meal and recipe is not None:
        return recipe.name
    return _plain_staple_name(slots.staple_kind)


def _staple_quantity(recipe: RecipeContext | None, kind: str, diners: int) -> tuple[str, str]:
    if kind == "米饭":
        return f"生米约 {RAW_RICE_GRAMS * diners}g", f"熟米饭约 {COOKED_RICE_GRAMS * diners}g"
    if kind == "面食" and (recipe is None or _uses_dry_noodles(recipe)):
        return f"干面约 {DRY_NOODLE_GRAMS * diners}g", ""
    return f"按 {diners} 人份准备", ""


def _uses_dry_noodles(recipe: RecipeContext) -> bool:
    return any(line.key == "noodles" or "面" in line.name for line in recipe.ingredients)


def _structure_text(slots: MealSlots, staple_label: str) -> str:
    parts: list[str] = []
    if slots.flexible:
        parts.append("1 道主菜")
    if slots.protein:
        parts.append(f"{slots.protein} 荤")
    if slots.vegetable:
        parts.append(f"{slots.vegetable} 素")
    if slots.soup:
        parts.append(f"{slots.soup} 汤")
    if staple_label:
        parts.append(staple_label)
    return " + ".join(parts)


def _scaled_lines(
    recipe: RecipeContext | None, diners: int, staple_kind: str | None
) -> list[dict[str, str]]:
    if recipe is None:
        return []
    if recipe.portion_confidence == "servings_only" and staple_kind is None:
        return [
            {"name": line.name, "display": f"按 {diners} 人份准备"} for line in _main_lines(recipe)
        ]
    rows: list[dict[str, str]] = []
    for line in _main_lines(recipe):
        rows.append(
            {"name": line.name, "display": _line_display(line, recipe, diners, staple_kind)}
        )
    return rows


def _main_lines(recipe: RecipeContext) -> list[Any]:
    return [line for line in recipe.ingredients if not line.optional]


def _line_display(line: Any, recipe: RecipeContext, diners: int, staple_kind: str | None) -> str:
    if staple_kind == "米饭" and (line.key == "rice" or "米" in line.name):
        return f"{RAW_RICE_GRAMS * diners}g"
    if staple_kind == "面食" and (line.key == "noodles" or "面" in line.name):
        return f"{DRY_NOODLE_GRAMS * diners}g"
    if _is_vague(line) or recipe.servings <= 0:
        return f"按 {diners} 人份准备"
    scaled = scale_quantity(Decimal(str(line.quantity)), recipe.servings, diners)
    unit = line.unit or ""
    return f"{_format_quantity(scaled)}{unit}"


def _is_vague(line: Any) -> bool:
    unit = str(line.unit or "")
    return line.quantity is None or not unit.strip() or any(token in unit for token in _VAGUE)


def _format_quantity(value: Decimal) -> str:
    text = f"{value.quantize(Decimal('0.001')):f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text
