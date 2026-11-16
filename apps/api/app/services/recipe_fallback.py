"""Supplement a missing named dish with a web tutorial or the configured LLM.

The caller still performs the deterministic restriction and inventory checks.
This module only normalizes cooking content and never decides whether a recipe
is safe for the household.
"""

from __future__ import annotations

import asyncio
import json
import re

from app.core.config import get_settings
from app.llm.factory import build_llm_provider
from app.llm.provider import LLMProvider
from app.services.ingredient_labels import clean_ingredient_names, strip_parenthetical_notes
from app.services.web_recipe_search import search_with_briefs

_WEB_RESULT_LIMIT = 2
_MAX_SOURCE_CHARS = 500

_RECIPE_PROMPT = """你是家庭菜谱助手。根据菜名和可选的网络搜索摘要，用中文整理一份常见家常做法。
只返回一个 JSON 对象，不要 Markdown、代码块或解释：
{"title":"菜名","servings":2,"ingredients":[{"name":"食材","amount":"大致用量"}],"steps":["步骤1","步骤2"]}
要求：
- `ingredients` 的 `name` 只写单个食材名，不得包含括号、产地/新鲜度、价格、促销、用量或替代说明。
- 至少列出 1 种食材和 2 个步骤，步骤要能直接照着做。
- 可以整理网络摘要中的信息，也可以使用常见家常做法；不要声称“已核实”。
- 用量可以写“适量”或家常用量，不要虚构精确营养数据。
- 不要省略可能引起过敏的食材。
"""


def _source_context(web_items: list[dict]) -> str:
    chunks: list[str] = []
    for index, item in enumerate(web_items, 1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        ingredients = strip_parenthetical_notes(str(item.get("ingredients") or "").strip())
        steps = "；".join(
            str(step).strip() for step in item.get("steps_brief") or [] if str(step).strip()
        )
        chunks.append(
            "\n".join(part for part in (
                f"{index}. 标题：{title or '未命名'}",
                f"食材：{ingredients[:300]}" if ingredients else "",
                f"步骤梗概：{steps[:300]}" if steps else "",
                f"来源：{item.get('url') or ''}" if item.get("url") else "",
            ) if part)
        )
    return "\n\n".join(chunks)[:_MAX_SOURCE_CHARS * 2]


def _user_prompt(dish: str, web_items: list[dict]) -> str:
    context = _source_context(web_items)
    if context:
        return f"菜名：{dish}\n\n网络搜索摘要（仅作参考，可能不完整）：\n{context}"
    return f"菜名：{dish}\n\n没有可用的网络搜索结果，请用常见家常做法补充。"


def _parse_recipe(raw: str, dish: str, web_items: list[dict]) -> dict | None:
    """Parse the strict JSON contract and normalize it for inventory analysis."""
    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    title = str(data.get("title") or dish).strip()[:120] or dish
    servings = data.get("servings")
    if isinstance(servings, str) and servings.strip().isdigit():
        servings = int(servings.strip())
    if not isinstance(servings, int) or not 1 <= servings <= 12:
        servings = 2

    raw_ingredients = data.get("ingredients")
    if isinstance(raw_ingredients, str):
        raw_ingredients = [raw_ingredients]
    ingredients: list[dict] = []
    seen_names: set[str] = set()
    for item in raw_ingredients or []:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            amount = str(item.get("amount") or "").strip()
            optional = bool(item.get("optional", False))
        else:
            name = str(item).strip()
            amount = ""
            optional = False
        if not name:
            continue
        for index, cleaned in enumerate(clean_ingredient_names(name)):
            if cleaned in seen_names:
                continue
            seen_names.add(cleaned)
            ingredients.append({
                "name": cleaned,
                # No catalog key is known here. Inventory matching must use the name;
                # a made-up key would break the future shopping-to-stock path.
                "ingredient_key": "",
                "amount": amount if index == 0 else "",
                "optional": optional,
            })

    raw_steps = data.get("steps")
    if isinstance(raw_steps, str):
        raw_steps = [raw_steps]
    steps: list[str] = []
    for item in raw_steps or []:
        value = re.sub(r"^\s*\d+[.、)]\s*", "", str(item)).strip()
        value = re.sub(r"\s+", " ", value)
        if value:
            steps.append(value[:240])
    if not ingredients or len(steps) < 2:
        return None

    first_source = next((item for item in web_items if isinstance(item, dict)), None)
    return {
        "name": title,
        "servings": servings,
        "ingredients": ingredients[:20],
        "steps": steps[:15],
        "allergens": [],
        "capabilities": {"quantities": False},
        "source_name": "网络教程" if first_source else "模型补充",
        "source_url": str(first_source.get("url") or "") if first_source else "",
        "unverified": True,
        "fallback_kind": "web" if first_source else "llm",
    }


async def lookup_recipe_tutorial(
    dish: str,
    *,
    provider: LLMProvider | None = None,
    web_enabled: bool | None = None,
) -> dict | None:
    """Try a web recipe summary, then let the LLM normalize or supplement it.

    A missing provider returns None before any network request so tests and
    unconfigured deployments stay offline.
    """
    dish = dish.strip()
    if not dish:
        return None
    # A tool-only provider cannot write a tutorial; fall back to the configured model.
    if provider is not None and not hasattr(provider, "chat"):
        provider = None
    settings = get_settings()
    provider = provider or build_llm_provider(settings)
    if provider is None:
        return None
    # Worker providers can tell us the model is unconfigured; skip the web request.
    ready = getattr(provider, "ready", None)
    if callable(ready) and not ready():
        return None
    if web_enabled is None:
        web_enabled = settings.recipe_web_fallback_enabled

    web_items: list[dict] = []
    if web_enabled:
        try:
            web_items = await asyncio.to_thread(search_with_briefs, dish, _WEB_RESULT_LIMIT)
        except Exception:  # noqa: BLE001 - web search is best-effort; the model is next
            web_items = []
    if not isinstance(web_items, list):
        web_items = []

    messages = [
        {"role": "system", "content": _RECIPE_PROMPT},
        {"role": "user", "content": _user_prompt(dish, web_items)},
    ]
    try:
        parsed = _parse_recipe(await provider.chat(messages=messages), dish, web_items)
        if parsed is None:
            parsed = _parse_recipe(
                await provider.chat(messages=[
                    *messages,
                    {"role": "user", "content": "上次输出无法解析。请只输出一个 JSON 对象。"},
                ]),
                dish,
                web_items,
            )
    except Exception:  # noqa: BLE001 - external model failure becomes a retryable message
        return None
    return parsed