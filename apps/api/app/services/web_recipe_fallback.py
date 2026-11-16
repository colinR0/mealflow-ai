"""Web recipe fallback: the model extracts ingredients, the server checks restrictions."""

from __future__ import annotations

from app.services.food_constraints import HouseholdConstraint, check_recipe
from app.services.ingredient_labels import clean_ingredient_names


def _status(check) -> str:
    if check is None:
        return "cannot_determine"
    return "matched" if check.hits else "not_detected_unverified"


def check_web_recipe(
    constraints: list[HouseholdConstraint],
    *,
    title: str,
    source_url: str,
    ingredients: list[str],
) -> dict:
    name = title.strip() or "未命名网络菜谱"
    url = source_url.strip()
    names: list[str] = []
    for item in ingredients:
        for name in clean_ingredient_names(item):
            if name not in names:
                names.append(name)
    if not url or not names:
        return {
            "template": "web_fallback",
            "status": "cannot_determine",
            "title": name,
            "source_url": url or None,
            "ingredients": names,
            "line": "忌口校验：无法判断：网络菜谱缺少来源或食材；未核实，不能推荐",
            "blocked": False,
        }
    check = check_recipe(constraints, ingredient_names=names)
    if check.blocked:
        hit = "；".join(
            f"{item.member_name}·{item.label}" for item in dict.fromkeys(check.hits)
        )
        line = f"忌口校验：已匹配：{hit}；网络菜谱未核实，不可推荐"
        blocked = True
    elif check.hits:
        hit = "；".join(
            f"{item.member_name}·{item.label}" for item in dict.fromkeys(check.hits)
        )
        line = f"忌口校验：已匹配：{hit}；网络菜谱未核实，请更换菜谱"
        blocked = False
    else:
        line = "忌口校验：未检出已登记过敏/忌口；仅为搜索摘要，未经全文核实，不能视为安全"
        blocked = False
    return {
        "template": "web_fallback",
        "status": _status(check),
        "title": name,
        "source_url": url,
        "ingredients": names,
        "line": line,
        "blocked": blocked,
    }


def render_web_recipe(payload: dict, *, source_name: str, ingredients: list[str]) -> str:
    source = source_name.strip() or "网络来源"
    lines = [
        f"【网络菜谱（未核实）】{payload['title']}",
        f"来源：{source}",
        payload["line"],
    ]
    if ingredients:
        lines.append("食材：" + "、".join(ingredients))
    lines.append("网络资料未经全文核实；过敏结论不能替代专业判断。")
    return "\n".join(lines)