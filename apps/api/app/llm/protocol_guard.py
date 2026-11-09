"""把模型误写入正文的工具协议还原成内部调用，并拦住泄漏文本。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TOOLS = {
    "get_member_constraints",
    "get_inventory_summary",
    "search_recipes",
    "search_cooking_skills",
    "find_ingredient_substitute",
    "get_flavor_template",
    "check_food_risk",
    "get_recipe_detail",
    "check_recipe_availability",
    "get_preference_summary",
    "recommend_menu",
}

_REQUIRED_TEXT = {
    "search_recipes": "query",
    "search_cooking_skills": "query",
    "find_ingredient_substitute": "query",
    "get_flavor_template": "query",
    "check_food_risk": "query",
    "get_recipe_detail": "recipe",
    "check_recipe_availability": "recipe",
}

_ALIAS_KEYS = {
    "getrecipebyid": "get_recipe_detail",
    "getrecipe": "get_recipe_detail",
    "recipedetail": "get_recipe_detail",
    "getrecipedetail": "get_recipe_detail",
    "findsubstitute": "find_ingredient_substitute",
    "getflavortemplate": "get_flavor_template",
    "checkfoodrisk": "check_food_risk",
}

_LEAK = re.compile(
    r"<\s*[|｜]\s*DSML\s*[|｜]|invoke\s+name\s*=|tool_calls",
    re.IGNORECASE,
)
_INVOKE = re.compile(
    r"<\s*[|｜]\s*DSML\s*[|｜]\s*invoke\b([^>]*)>(.*?)"
    r"<\s*/\s*[|｜]\s*DSML\s*[|｜]\s*invoke\s*>",
    re.IGNORECASE | re.DOTALL,
)
_PARAM = re.compile(
    r"<\s*[|｜]\s*DSML\s*[|｜]\s*parameter\b([^>]*)>(.*?)"
    r"<\s*/\s*[|｜]\s*DSML\s*[|｜]\s*parameter\s*>",
    re.IGNORECASE | re.DOTALL,
)
_NAME = re.compile(r"""name\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def contains_protocol_leak(text: str) -> bool:
    return bool(_LEAK.search(text))


def normalize_model_message(
    message: dict[str, Any], allowed: set[str] | None = None
) -> dict[str, Any]:
    """标准 tool_calls 优先；正文里的 DSML / JSON 信封可还原，无法执行的调用直接丢掉。"""
    allowed_names = DEFAULT_TOOLS if allowed is None else allowed
    content = str(message.get("content") or "")
    existing = [item for item in message.get("tool_calls") or [] if isinstance(item, dict)]
    parsed = [] if existing else _parse_dsml(content) + _parse_json_envelope(content)
    valid: list[dict[str, Any]] = []
    for index, raw in enumerate(existing or parsed):
        call = _coerce_call(raw, index)
        if call is None:
            continue
        function = call["function"]
        original = str(function.get("name") or "")
        name = _alias(original)
        if name != original:
            logger.info("工具名 %s 已映射为 %s", original, name)
            function["name"] = name
        if name not in allowed_names:
            logger.warning("忽略未登记工具：%s", original or name)
            continue
        arguments = str(function.get("arguments") or "{}")
        if not _arguments_ok(name, arguments):
            logger.warning("工具参数无效，未执行：%s", name)
            continue
        valid.append(call)
    leaked = contains_protocol_leak(content)
    if valid and leaked:
        content = ""
    updated = dict(message)
    updated["content"] = content
    updated["tool_calls"] = valid
    return updated


def _alias(name: str) -> str:
    compact = re.sub(r"[^a-z0-9]", "", name.lower())
    return _ALIAS_KEYS.get(compact, name.strip())


def _arguments_ok(name: str, arguments: str) -> bool:
    try:
        data = json.loads(arguments or "{}")
    except json.JSONDecodeError:  # Reject malformed model arguments before tool execution.
        return False
    if not isinstance(data, dict):
        return False
    field = _REQUIRED_TEXT.get(name)
    if field and not str(data.get(field) or "").strip():
        return False
    return True


def _parse_dsml(text: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for index, (attrs, body) in enumerate(_INVOKE.findall(text)):
        found = _NAME.search(attrs)
        if not found:
            continue
        calls.append(_make_call(found.group(1), _params(body), index))
    return calls


def _params(body: str) -> dict[str, Any]:
    pairs = _PARAM.findall(body)
    if pairs:
        result: dict[str, Any] = {}
        for attrs, raw in pairs:
            found = _NAME.search(attrs)
            if not found:
                continue
            result[found.group(1)] = _coerce_param(raw)
        return result
    match = re.search(r"\{.*\}", body, re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:  # Unparseable embedded parameters cannot become tool arguments.
        return {}
    return data if isinstance(data, dict) else {}


def _coerce_param(raw: str) -> Any:
    text = raw.strip()
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return text


def _parse_json_envelope(text: str) -> list[dict[str, Any]]:
    if "tool_calls" not in text:
        return []
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:  # Ignore malformed model envelopes rather than executing them.
        return []
    if not isinstance(data, dict):
        return []
    raw_calls = data.get("tool_calls")
    if isinstance(raw_calls, list):
        return [item for item in raw_calls if isinstance(item, dict)]
    if data.get("name"):
        arguments = data.get("arguments") if data.get("arguments") is not None else {}
        if not isinstance(arguments, dict):
            arguments = {}
        return [_make_call(str(data["name"]), arguments, 0)]
    return []


def _make_call(name: str, arguments: dict[str, Any], index: int) -> dict[str, Any]:
    return _coerce_call({"name": name, "arguments": arguments}, index) or {}


def _coerce_call(raw: dict[str, Any], index: int) -> dict[str, Any] | None:
    function = raw.get("function")
    if isinstance(function, dict):
        name = str(function.get("name") or "")
        arguments = function.get("arguments")
    else:
        name = str(raw.get("name") or "")
        arguments = raw.get("arguments")
        if arguments is None:
            arguments = raw.get("parameters") or {}
    if not name:
        return None
    if isinstance(arguments, dict):
        argument_text = json.dumps(arguments, ensure_ascii=False)
    else:
        argument_text = str(arguments or "{}")
    return {
        "id": str(raw.get("id") or f"call-norm-{index}"),
        "type": "function",
        "function": {"name": name, "arguments": argument_text},
    }
