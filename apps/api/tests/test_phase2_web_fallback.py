"""Web recipe fallback keeps allergen wording deterministic and never safe."""

from app.services.food_constraints import HouseholdConstraint
from app.services.web_recipe_fallback import check_web_recipe, render_web_recipe

PEANUT = HouseholdConstraint("p1", "妈妈", "allergy", "peanut", "花生", ("peanut", "花生"))


def test_allergy_match_blocks_and_never_says_safe():
    result = check_web_recipe(
        [PEANUT],
        title="花生炖猪蹄",
        source_url="https://example.com/recipe",
        ingredients=["猪蹄", "花生酱"],
    )
    assert result["status"] == "matched"
    assert result["blocked"] is True
    assert "妈妈·花生" in result["line"]
    assert "不可推荐" in result["line"]
    assert "安全" not in result["line"]
    rendered = render_web_recipe(
        result, source_name="example.com", ingredients=result["ingredients"]
    )
    assert "【网络菜谱（未核实）】" in rendered
    assert "来源：example.com" in rendered


def test_no_match_is_unverified_not_a_pass():
    result = check_web_recipe(
        [PEANUT],
        title="番茄炒蛋",
        source_url="https://example.com/eggs",
        ingredients=["番茄", "鸡蛋"],
    )
    assert result["status"] == "not_detected_unverified"
    assert result["blocked"] is False
    assert "不能视为安全" in result["line"]
    assert "通过" not in result["line"]


def test_missing_source_or_ingredients_is_cannot_determine():
    result = check_web_recipe(
        [PEANUT], title="未知菜", source_url="", ingredients=["鸡蛋"]
    )
    assert result["status"] == "cannot_determine"
    assert "无法判断" in result["line"]
    assert "安全" not in result["line"]


def test_ingredient_descriptors_and_kitchenware_are_dropped_before_matching():
    result = check_web_recipe(
        [PEANUT],
        title="荔浦芋头",
        source_url="https://example.com/taro",
        ingredients=["荔浦芋头、实惠新鲜）", "盆"],
    )
    assert result["ingredients"] == ["荔浦芋头"]