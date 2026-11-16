"""Server-rendered quick actions for inventory, restrictions and skills.

The client never writes the safety sentence: it is rendered here from a
deterministic household-restriction check. The LLM may supplement a missing
recipe, but it never decides whether a restriction is hit.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Ingredient, Recipe, RecipeIngredient
from app.db.v2_models import InventoryLot, KnowledgeDocument
from app.llm.factory import build_llm_provider
from app.llm.provider import LLMProvider
from app.services.food_constraints import (
    ConstraintCheck,
    HouseholdConstraint,
    check_recipe,
    household_constraints,
)
from app.services.ingredient_labels import clean_corpus_names

DISCLAIMER = "仅核对已登记的过敏/忌口，不代表饮食安全；如对成分不确定，请查看原包装或咨询专业人士。"


@dataclass
class QuickAction:
    template: Literal["local", "offline", "web", "no_result"]
    text: str
    recipes: list[dict]
    skills: list[dict]
    evidence: list[dict]
    citations: list[str]
    check: dict
    data_version: str | None = None
    fallback_allowed: bool = False


def data_version(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _unchecked_payload() -> dict:
    return {
        "status": "no_result",
        "blocked": False,
        "line": "忌口校验：未执行：未选出可校验的菜谱；结论：无法判断",
        "hits": [],
    }


def _check_payload(check: ConstraintCheck) -> dict:
    return {
        "status": check.status,
        "blocked": check.blocked,
        "line": check.line(),
        "hits": [asdict(hit) for hit in dict.fromkeys(check.hits)],
    }


def _content_ingredients(content: object) -> list[dict]:
    """Read normalized document ingredients without inventing amounts."""
    if not isinstance(content, dict):
        return []
    result = []
    for item in content.get("ingredients") or []:
        if isinstance(item, str):
            for name in clean_corpus_names(item):
                result.append({
                    "key": name, "name": name, "quantity": None,
                    "unit": None, "optional": False,
                })
            continue
        if not isinstance(item, dict):
            continue
        raw_name = str(item.get("name") or "").strip()
        key = str(item.get("ingredient_key") or item.get("key") or "").strip()
        if not raw_name and not key:
            continue
        names = clean_corpus_names(raw_name) if raw_name else clean_corpus_names(key)
        if not names:
            continue
        quantity = item.get("quantity")
        if isinstance(quantity, Decimal):
            quantity = float(quantity)
        optional = bool(item.get("optional", False))
        for index, name in enumerate(names):
            result.append({
                "key": (key or name) if index == 0 else name,
                "name": name,
                # The amount belongs to the whole source line; never duplicate it.
                "quantity": quantity if index == 0 else None,
                "unit": item.get("unit") if index == 0 else None,
                "optional": optional,
            })
    return result


def recipe_payloads(session: Session) -> list[dict]:
    recipes = session.scalars(select(Recipe).order_by(Recipe.name, Recipe.id)).all()
    ids = [recipe.id for recipe in recipes]
    rows = session.execute(
        select(RecipeIngredient, Ingredient)
        .join(Ingredient, Ingredient.id == RecipeIngredient.ingredient_id)
        .where(RecipeIngredient.recipe_id.in_(ids))
        .order_by(RecipeIngredient.optional, Ingredient.name)
    ).all() if ids else []
    documents = {
        document.recipe_id: document
        for document in session.scalars(
            select(KnowledgeDocument).where(KnowledgeDocument.recipe_id.in_(ids))
        ).all()
    } if ids else {}
    by_recipe: dict[str, list[dict]] = {recipe_id: [] for recipe_id in ids}
    for link, ingredient in rows:
        by_recipe[link.recipe_id].append({
            "key": ingredient.key,
            "name": ingredient.name,
            "quantity": float(link.quantity) if link.quantity is not None else None,
            "unit": link.unit,
            "optional": bool(link.optional),
        })
    result = []
    for recipe in recipes:
        document = documents.get(recipe.id)
        content = document.content if document is not None else {}
        # The import stores normalized ingredients in JSON. Use them until the
        # relational links are materialized so quick actions and bundles work.
        if not by_recipe[recipe.id]:
            by_recipe[recipe.id] = _content_ingredients(content)
        category = str(content.get("category") or "其他") if isinstance(content, dict) else "其他"
        result.append({
            "id": recipe.id,
            "name": recipe.name,
            "servings": recipe.servings,
            "duration_minutes": recipe.duration_minutes,
            "category": category,
            "tags": list(recipe.tags or []),
            "taste_tags": list(recipe.taste_tags or []),
            "allergens": list(recipe.allergens or []),
            "steps": [str(step) for step in (recipe.steps or [])],
            "source_name": recipe.source_name,
            "source_url": recipe.source_url,
            "license_note": recipe.license_note,
            "ingredients": by_recipe[recipe.id],
        })
    return result


def published_recipe_ids(session: Session) -> set[str]:
    return set(session.scalars(
        select(KnowledgeDocument.recipe_id).where(
            KnowledgeDocument.published.is_(True),
            KnowledgeDocument.recipe_id.is_not(None),
        )
    ).all())


def _recipe_haystack(recipe: dict) -> str:
    return " ".join([
        recipe["name"], recipe["category"], *recipe["tags"], *recipe["taste_tags"],
        *(item["name"] for item in recipe["ingredients"]),
        *(item["key"] for item in recipe["ingredients"]),
    ]).lower()


def browse_recipes(
    session: Session, query: str = "", limit: int = 20, offset: int = 0,
) -> dict:
    """Return approved recipe summaries for the read-only browse page."""
    published = published_recipe_ids(session)
    recipes = [item for item in recipe_payloads(session) if item["id"] in published]
    tokens = [
        token.lower()
        for token in query.replace("，", " ").replace("、", " ").split()
        if token
    ]
    if tokens:
        recipes = [
            item for item in recipes
            if all(token in _recipe_haystack(item) for token in tokens)
        ]
    total = len(recipes)
    page = recipes[offset:offset + limit]
    items = [{
        "id": item["id"],
        "name": item["name"],
        "servings": item["servings"],
        "duration_minutes": item["duration_minutes"],
        "category": item["category"],
        "tags": item["tags"],
        "taste_tags": item["taste_tags"],
        "allergens": item["allergens"],
        "source_name": item["source_name"],
        "source_url": item["source_url"],
        "license_note": item["license_note"],
        "ingredient_names": [ingredient["name"] for ingredient in item["ingredients"]],
        "ingredient_count": len(item["ingredients"]),
    } for item in page]
    return {
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(items) < total,
    }


def get_recipe(session: Session, recipe_id: str) -> dict | None:
    if recipe_id not in published_recipe_ids(session):
        return None
    return next(
        (item for item in recipe_payloads(session) if item["id"] == recipe_id),
        None,
    )


def skill_payloads(session: Session) -> list[dict]:
    documents = session.scalars(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.published.is_(True), KnowledgeDocument.document_type != "recipe")
        .order_by(KnowledgeDocument.title, KnowledgeDocument.id)
    ).all()
    result = []
    for document in documents:
        content = document.content or {}
        text = content.get("markdown") or content.get("text") or document.search_text
        result.append({
            "id": document.id,
            "title": document.title or "烹饪技巧",
            "document_type": document.document_type,
            "content_version": document.content_version,
            "source_name": content.get("source_name") or "",
            "source_url": content.get("source_url") or "",
            "text": str(text)[:12000],
        })
    return result


def _recipe_check(recipe: dict, constraints) -> ConstraintCheck:
    if not recipe["ingredients"]:
        return ConstraintCheck(
            [], bool(constraints),
            "忌口校验：未执行：菜谱食材信息不完整；结论：无法判断",
        )
    return check_recipe(
        constraints,
        ingredient_names=[item["name"] for item in recipe["ingredients"]],
        ingredient_keys=[item["key"] for item in recipe["ingredients"]],
        allergens=recipe["allergens"],
        taste_tags=recipe["taste_tags"],
    )


def _evidence(recipe: dict) -> dict:
    return {
        "id": recipe["id"],
        "kind": "recipe",
        "title": recipe["name"],
        "source": recipe["source_name"],
        "url": recipe["source_url"],
    }


def _recipe_text(
    recipe: dict,
    check: ConstraintCheck,
    *,
    heading: str,
    reason: str,
    offline: bool,
    data_version: str | None,
) -> str:
    lines = [f"{heading}{recipe['name']}", check.line()]
    if reason:
        lines.append(f"理由：{reason}")
    source = recipe["source_name"] or "菜谱库"
    if recipe["source_url"]:
        source += f"（{recipe['source_url']}）"
    lines.append(f"来源：{source}")
    if check.blocked:
        lines.append("说明：已命中过敏/忌口，不能通过替换食材规避；请更换菜谱。")
        return "\n".join(lines)
    if recipe["steps"]:
        lines.append("步骤：")
        lines.extend(f"{index}. {step}" for index, step in enumerate(recipe["steps"], 1))
    if offline:
        lines.append(f"离线数据版本：{data_version or '未知'}；离线模式不生成新内容。")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def _skill_text(skill: dict, *, offline: bool, data_version: str | None) -> str:
    lines = [f"【检索烹饪技巧】{skill['title']}", f"来源：{skill['source_name'] or '知识库'}"]
    if skill["source_url"]:
        lines[-1] += f"（{skill['source_url']}）"
    lines.extend(["", skill["text"]])
    if offline:
        lines.append(f"\n离线数据版本：{data_version or '未知'}；离线模式不生成新内容。")
    return "\n".join(lines)


def _inventory_candidates(
    session: Session, workspace_id: str, recipes: list[dict],
) -> list[tuple[int, dict, list[str]]]:
    stock = session.scalars(
        select(InventoryLot).where(
            InventoryLot.workspace_id == workspace_id,
            InventoryLot.status == "active",
        )
    ).all()
    stock_keys = {item.ingredient_key for item in stock}
    stock_names = {item.name for item in stock}
    candidates = []
    for recipe in recipes:
        matched = [
            item["name"] for item in recipe["ingredients"]
            if item["key"] in stock_keys or item["name"] in stock_names
        ]
        if matched:
            candidates.append((len(matched), recipe, matched))
    candidates.sort(key=lambda item: (-item[0], item[1]["name"]))
    return candidates


def _restriction_candidates(
    recipes: list[dict], query: str,
) -> list[tuple[int, dict, list[str]]]:
    tokens = [token for token in query.replace("，", " ").split() if token]
    if not tokens:
        # An empty query used to match every recipe and returned an unrelated
        # first row (for example a cocktail). Require a dish or ingredient.
        return []
    candidates = []
    for recipe in recipes:
        haystack = " ".join([
            recipe["name"], *recipe["taste_tags"],
            *(item["name"] for item in recipe["ingredients"]),
        ])
        if all(token in haystack for token in tokens):
            candidates.append((0, recipe, []))
    return candidates


def quick_action(
    session: Session,
    workspace_id: str,
    action: Literal["inventory", "restrictions", "skills"],
    query: str = "",
    *,
    offline: bool = False,
) -> QuickAction:
    if action == "skills":
        return _skill_action(session, query, offline=offline)
    recipes = recipe_payloads(session)
    version = data_version({"recipes": recipes})
    if action == "restrictions" and not query.strip():
        return QuickAction(
            "no_result",
            "【查忌口食谱】请先输入菜名或食材，再点检索。",
            [], [], [], [],
            _unchecked_payload(), version, False,
        )
    constraints = household_constraints(session, workspace_id)
    template: Literal["local", "offline"] = "offline" if offline else "local"
    if action == "inventory":
        heading = "【离线·按库存推荐】" if offline else "【按库存推荐】"
        candidates = _inventory_candidates(session, workspace_id, recipes)
        empty_text = "【按库存推荐】当前库存没有可用于匹配的已审核菜谱。"
    else:
        heading = "【离线·查忌口食谱】" if offline else "【查忌口食谱】"
        published = published_recipe_ids(session)
        candidates = _restriction_candidates(
            [recipe for recipe in recipes if recipe["id"] in published], query
        )
        empty_text = "【查忌口食谱】菜谱库中没有匹配的已发布菜谱。"
    if not candidates:
        return QuickAction(
            "no_result", empty_text, [], [], [], [],
            _unchecked_payload(), version, True,
        )
    checked = [(recipe, _recipe_check(recipe, constraints), matched)
               for _, recipe, matched in candidates]
    passing = [
        (recipe, check, matched) for recipe, check, matched in checked
        if not check.blocked and check.status != "cannot_determine"
    ]
    recipe, check, matched = passing[0] if passing else checked[0]
    if check.blocked and action == "inventory":
        reason = "已命中过敏/忌口，不能推荐该菜谱。"
    elif matched:
        reason = f"使用库存中的{'、'.join(matched)}"
    else:
        reason = "与查询匹配"
    text = _recipe_text(
        recipe, check, heading=heading, reason=reason,
        offline=offline, data_version=version,
    )
    return QuickAction(
        template, text, [recipe], [], [_evidence(recipe)], [recipe["id"]],
        _check_payload(check), version, not check.blocked and check.status != "cannot_determine",
    )


_LLM_RECIPE_PROMPT = """你是家庭菜谱助手。根据用户给出的菜名，用中文给出常见家常做法。
只返回一个 JSON 对象，不要 Markdown、代码块或解释：
{"title":"菜名","ingredients":[{"name":"食材","amount":"大致用量"}],"steps":["步骤1","步骤2"]}
要求：
- 列出所有主要食材和调料，可能引起过敏的食材不要省略。
- 用量可以写“适量”，不要虚构精确营养数据。
- 不要输出“安全”“已核实”等结论。
"""


def _parse_llm_recipe(raw: str, query: str) -> tuple[str, list[str], list[str]] | None:
    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    title = str(data.get("title") or query).strip() or query
    raw_ingredients = data.get("ingredients")
    if isinstance(raw_ingredients, str):
        raw_ingredients = [raw_ingredients]
    ingredients: list[str] = []
    for item in raw_ingredients or []:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            amount = str(item.get("amount") or "").strip()
            value = f"{name} {amount}".strip()
        else:
            value = str(item).strip()
        if value:
            ingredients.append(value)
    raw_steps = data.get("steps")
    if isinstance(raw_steps, str):
        raw_steps = [raw_steps]
    steps: list[str] = []
    for item in raw_steps or []:
        value = re.sub(r"^\s*\d+[.、)]\s*", "", str(item)).strip()
        value = re.sub(r"\s+", " ", value)
        if value:
            steps.append(value[:240])
    if not ingredients or not steps:
        return None
    return title[:120], ingredients[:16], steps[:12]


def _fallback_failure(version: str, text: str) -> QuickAction:
    return QuickAction(
        "no_result", text, [], [], [], [], _unchecked_payload(), version, False,
    )


async def llm_recipe_action(
    constraints: list[HouseholdConstraint],
    query: str,
    *,
    version: str,
    provider: LLMProvider | None = None,
) -> QuickAction | None:
    """Supplement a missing local recipe without letting the model decide safety."""
    query = query.strip()
    if not query:
        return None
    provider = provider or build_llm_provider(get_settings())
    if provider is None:
        return None
    messages = [
        {"role": "system", "content": _LLM_RECIPE_PROMPT},
        {"role": "user", "content": f"菜名：{query}"},
    ]
    try:
        parsed = _parse_llm_recipe(await provider.chat(messages=messages), query)
        if parsed is None:
            parsed = _parse_llm_recipe(await provider.chat(messages=[
                *messages,
                {"role": "user", "content": "上次输出无法解析。请只输出一个 JSON 对象。"},
            ]), query)
    except Exception:
        # External model failures must become actionable copy instead of a 500.
        return _fallback_failure(
            version,
            f"【查忌口食谱】本地菜谱库未收录「{query}」，模型补充暂时不可用，请稍后重试。",
        )
    if parsed is None:
        return _fallback_failure(
            version,
            f"【查忌口食谱】本地菜谱库未收录「{query}」，模型返回内容不完整，请换一种菜名再试。",
        )
    title, ingredients, steps = parsed
    check = check_recipe(constraints, ingredient_names=ingredients)
    check_payload = _check_payload(check)
    if check.blocked:
        line = check.line()
    elif check.hits:
        line = check.line().replace("；结论：通过", "；模型做法未核实，建议更换菜谱")
    elif check.has_constraints:
        line = "忌口校验：已对照模型列出的食材；未命中已登记过敏/忌口；模型做法未核实，不能视为安全"
    else:
        line = "忌口校验：未执行：无已登记过敏/忌口；模型做法未核实"
    check_payload["line"] = line
    check_payload["status"] = (
        "matched" if check.hits
        else "cannot_determine" if not check.has_constraints
        else "not_detected"
    )
    check_payload["blocked"] = check.blocked
    if check.blocked:
        lead = (
            f"【查忌口食谱】本地菜谱库未收录「{query}」，已用模型补充《{title}》；"
            "但命中已登记过敏/忌口，不能推荐。"
        )
    elif check.hits:
        lead = (
            f"【查忌口食谱】本地菜谱库未收录「{query}」，已用模型补充《{title}》；"
            "命中已登记忌口，建议更换菜谱。"
        )
    else:
        lead = f"【查忌口食谱】本地菜谱库未收录「{query}」，已用模型补充《{title}》。"
    lines = [
        lead,
        line,
        "食材：" + "、".join(ingredients),
        "步骤：",
        *(f"{index}. {step}" for index, step in enumerate(steps, 1)),
        "模型补充做法未经来源核实；忌口结论仅依据模型列出的食材，请以实际配料为准。",
    ]
    recipe_id = "llm:" + hashlib.sha256(
        f"{query}\n{title}\n{'|'.join(ingredients)}".encode()
    ).hexdigest()[:24]
    recipe = {
        "id": recipe_id,
        "name": title,
        "category": "模型补充",
        "ingredients": ingredients,
        "steps": steps,
        "source_name": "LLM 通用做法（未核实）",
        "source_url": None,
    }
    return QuickAction(
        "web", "\n".join(lines), [recipe], [], [], [], check_payload, version, False,
    )


def _skill_action(
    session: Session, query: str, *, offline: bool,
) -> QuickAction:
    skills = skill_payloads(session)
    version = data_version({"skills": skills})
    tokens = [token for token in query.replace("，", " ").split() if token]
    if not tokens:
        return QuickAction(
            "no_result", "【检索烹饪技巧】请先输入烹饪问题或技巧关键词。",
            [], [], [], [], {"status": "no_result", "blocked": False, "line": "", "hits": []},
            version, False,
        )
    matched = [
        skill for skill in skills
        if not tokens or all(token in f"{skill['title']} {skill['text']}" for token in tokens)
    ]
    if not matched:
        return QuickAction(
            "no_result", "【检索烹饪技巧】已下载知识库中没有匹配的技巧。",
            [], [], [], [], {"status": "no_result", "blocked": False, "line": "", "hits": []},
            version, True,
        )
    skill = matched[0]
    evidence = [{
        "id": skill["id"], "kind": "cooking_skill", "title": skill["title"],
        "source": skill["source_name"], "url": skill["source_url"],
    }]
    return QuickAction(
        "offline" if offline else "local",
        _skill_text(skill, offline=offline, data_version=version),
        [], [skill], evidence, [skill["id"]],
        {"status": "matched", "blocked": False, "line": "", "hits": []},
        version, False,
    )