from decimal import Decimal

from app.services.recipe_taxonomy import IngredientFact, classify_recipe


def _fact(
    key: str, name: str, category: str, quantity: str = "1", unit: str = "份"
) -> IngredientFact:
    return IngredientFact(key, name, category, Decimal(quantity), unit, False)


def test_named_dishes_match_manual_roles() -> None:
    cases = {
        "家常汤面": ("complete_meal", "面食"),
        "大锅米饭": ("staple_side", "米饭"),
        "黄焖鸡米饭": ("complete_meal", "米饭"),
        "洋葱炒蛋": ("mixed_dish", "无"),
        "清炒时蔬": ("vegetable_dish", "无"),
    }
    ingredients = {
        "家常汤面": [
            _fact("noodles", "挂面", "主食"),
            _fact("greens", "青菜", "蔬菜"),
            _fact("egg", "鸡蛋", "蛋奶"),
        ],
        "大锅米饭": [_fact("rice", "大米", "主食", "0.55", "kg")],
        "黄焖鸡米饭": [_fact("chicken_leg", "鸡腿", "肉禽"), _fact("rice", "大米", "主食")],
        "洋葱炒蛋": [_fact("onion", "洋葱", "蔬菜"), _fact("egg", "鸡蛋", "蛋奶")],
        "清炒时蔬": [_fact("greens", "青菜", "蔬菜")],
    }
    for name, expected in cases.items():
        classified = classify_recipe(name, [], ingredients[name])
        assert (classified.meal_role, classified.staple_type) == expected


def test_incomplete_recipe_is_not_composable() -> None:
    classified = classify_recipe("神秘调味", [], [_fact("salt", "盐", "调料")])
    assert classified.meal_role == ""
    assert classified.composable is False
