"""受控、可观测的对话工具循环。"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy.exc import OperationalError

from app.agent.intent import (
    find_recipe,
    generate_recipe,
    parse_meal_intent,
    upsert_member_preferences,
)
from app.llm.protocol_guard import contains_protocol_leak, normalize_model_message
from app.llm.provider import LLMProvider
from app.schemas.agent import ToolTraceView
from app.services.context_loader import PlanningContext, RecipeContext, load_planning_context
from app.services.menu_composer import (
    compose_menu,
    is_recipe_safe_for_menu,
    render_menu_reply,
    unfilled_menu_slots,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """你是 MealFlow 的家庭厨房对话 Agent。你可以读取家庭限制、冰箱、本地菜谱和历史偏好。只能使用工具已返回的事实，绝不编造库存、限制或菜谱。
如果用户肯定上一轮你提出的“帮你查某道菜/菜谱”建议，必须从会话上下文提取菜名并调用菜谱工具，不能重复提问。
用户要求整餐、几菜一汤或按人数配餐时，自主判断还缺少哪些事实，再调用 recommend_menu。根据工具结果决定是否需要继续读取详情或补充查询；菜品组合、主食互斥和份量以 recommend_menu 的结果为准，你只能解释，不能改菜，也不能把汤面、炒饭和另一种主食拼在一起。
不要输出工具协议、DSML、函数调用标记或 JSON 工具信封。读取菜谱只能使用 get_recipe_detail。缺少非辅料主料不是拒答理由：仍给出完整做法，并把缺项作为可选采购建议；不要要求用户先补货。常备料默认可用，但过敏或忌口不能绕过。你不能写库存、创建采购单或发布餐单。使用简洁自然的简体中文。"""

_WRAP_UP_PROMPT = "工具额度已用尽。不要调用任何工具，不要输出 DSML、tool_calls、invoke 或 JSON 工具信封。只用自然语言根据已有结果回答。最终回答不消耗工具额度。"

TOOL_META = {
    "get_member_constraints": ("检查用餐限制", "当前家庭成员的饮食限制"),
    "get_inventory_summary": ("读取冰箱库存", "可用食材与临期食材"),
    "search_recipes": ("检索本地菜谱", "查询“{query}”"),
    "get_recipe_detail": ("读取菜谱做法", "查看“{recipe}”的食材和步骤"),
    "check_recipe_availability": ("核对食材可用性", "核对“{recipe}”的库存与缺项"),
    "get_preference_summary": ("读取家庭偏好", "历史评分与偏好"),
    "upsert_member_preference": ("更新长期偏好", "写入已校验的家庭偏好"),
    "recommend_menu": ("生成本餐建议", "按库存与限制推荐 {dish_count} 道菜"),
}

def _tool(name: str, description: str, properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {"type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False}}}

TOOL_DEFINITIONS = [
    _tool("get_member_constraints", "读取当前用餐成员的过敏、忌口和特殊状态。"),
    _tool("get_inventory_summary", "读取冰箱中的可用和临期食材。"),
    _tool("search_recipes", "按菜名、食材或口味检索本地菜谱。", {"query": {"type": "string"}}, ["query"]),
    _tool("get_recipe_detail", "按菜谱 ID 或名称读取完整食材、步骤和份量。", {"recipe": {"type": "string"}}, ["recipe"]),
    _tool("check_recipe_availability", "核对一道菜的主料库存、缺少主料、常备辅料和限制。", {"recipe": {"type": "string"}}, ["recipe"]),
    _tool("get_preference_summary", "读取已记录的菜品评分和偏好摘要。"),
    _tool(
        "recommend_menu",
        "按人数、库存和限制生成整餐。dish_count 不含主食和单独的汤；四菜一汤传 dish_count=4 且 need_soup=true。不传 dish_count 时按人数使用默认荤素模板。",
        {
            "diners": {"type": "integer", "minimum": 1, "maximum": 12},
            "dish_count": {"type": "integer", "minimum": 1, "maximum": 8},
            "need_soup": {"type": "boolean"},
            "staple": {"type": "string"},
            "style": {"type": "string"},
            "vegetarian": {"type": "boolean"},
            "all_meat": {"type": "boolean"},
            "preferred_ingredients": {"type": "array", "items": {"type": "string"}},
            "required_dishes": {"type": "array", "items": {"type": "string"}},
            "excluded_dishes": {"type": "array", "items": {"type": "string"}},
            "excluded_ingredients": {"type": "array", "items": {"type": "string"}},
            "budget_limit_cents": {"type": "integer", "minimum": 1},
        },
    ),
]

_ALLOWED_TOOLS = {tool["function"]["name"] for tool in TOOL_DEFINITIONS}

@dataclass
class ConversationTurn:
    text: str
    traces: list[ToolTraceView] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    procurement_suggestions: list[dict[str, Any]] = field(default_factory=list)
    menu_recommendation: dict[str, Any] | None = None
    preference_updates: list[dict[str, str]] = field(default_factory=list)


def _suggestion_key(item: dict[str, Any]) -> str:
    """同一菜谱的同一缺项只向用户展示一次。"""
    source = str(item.get("source_id") or item.get("recipe_id") or "")
    ingredient = str(item.get("ingredient_key") or item.get("ingredient") or "")
    return f"{source}:{ingredient}"

def _load_context(workspace_id: str, site_id: str, meal_service_id: str) -> PlanningContext:
    from app.db.session import SessionLocal
    with SessionLocal() as session:
        return load_planning_context(session, workspace_id, site_id, meal_service_id)

def _recipe(context: PlanningContext, value: str) -> RecipeContext:
    value = value.strip().lower()
    for item in context.recipes:
        if item.id == value or item.name.lower() == value or value in item.name.lower(): return item
    raise ValueError("未找到对应本地菜谱")

def _availability(recipe: RecipeContext, context: PlanningContext, diner_ids: list[str] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    restricted = {r.ingredient_key for r in context.member_constraints if r.ingredient_key and (not diner_ids or not r.user_id or r.user_id in diner_ids)}
    groups: dict[str, list[dict[str, Any]]] = {x: [] for x in ("available_main_ingredients", "missing_main_ingredients", "pantry_staples", "optional_ingredients", "restricted_ingredients")}
    suggestions: list[dict[str, Any]] = []
    for line in recipe.ingredients:
        row = {"key": line.key, "name": line.name, "quantity": str(line.quantity), "unit": line.unit}
        if line.key in restricted: groups["restricted_ingredients"].append(row)
        elif line.optional: groups["optional_ingredients"].append(row)
        elif line.key in context.pantry_keys: groups["pantry_staples"].append(row)
        elif context.available_for(line.key) > 0: groups["available_main_ingredients"].append(row)
        else:
            groups["missing_main_ingredients"].append(row)
            suggestions.append({"ingredient": line.name, "quantity": str(line.quantity), "unit": line.unit, "recipe_id": recipe.id, "recipe_name": recipe.name, "source_id": f"chat:{recipe.id}", "reason": f"制作{recipe.name}缺少的主料"})
    return {"recipe_id": recipe.id, "recipe_name": recipe.name, **groups, "can_cook_without_purchase": not groups["missing_main_ingredients"] and not groups["restricted_ingredients"]}, suggestions

def _recommend_menu(args: dict[str, Any], context: PlanningContext, diner_ids: list[str] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return compose_menu(args, context, diner_ids)


def _generation_requirements(
    context: PlanningContext,
    diner_ids: list[str] | None,
    *,
    meal_role: str = "",
    style: str = "",
    excluded_ingredients: list[str] | None = None,
) -> dict[str, Any]:
    names_by_key: dict[str, set[str]] = {
        key: {name} for key, name in context.ingredient_names.items()
    }
    for recipe in context.recipes:
        for line in recipe.ingredients:
            names_by_key.setdefault(line.key, set()).add(line.name)
    active = [
        item
        for item in context.member_constraints
        if not diner_ids or not item.user_id or item.user_id in diner_ids
    ]
    forbidden = {
        name
        for item in active
        if item.level == "forbidden" and item.ingredient_key
        for name in names_by_key.get(item.ingredient_key, {item.ingredient_key})
    }
    available = [item.name for item in context.inventory if item.quantity > 0]
    pantry = sorted(
        name
        for key in context.pantry_keys
        for name in names_by_key.get(key, {key})
    )
    return {
        "meal_role": meal_role,
        "style": style,
        "excluded_ingredients": excluded_ingredients or [],
        "forbidden_ingredients": sorted(forbidden),
        "available_ingredients": available,
        "pantry_staples": pantry,
    }


def _accept_generated_recipe(
    recipe: RecipeContext,
    context: PlanningContext,
    diner_ids: list[str] | None,
    *,
    expected_role: str = "",
    excluded_ingredients: list[str] | None = None,
) -> bool:
    return (
        (not expected_role or recipe.meal_role == expected_role)
        and is_recipe_safe_for_menu(recipe, context, diner_ids, excluded_ingredients)
    )


def _safe_reply(text: str, menu: dict[str, Any] | None) -> str:
    cleaned = text.strip()
    if cleaned and not contains_protocol_leak(cleaned):
        return cleaned
    if cleaned:
        logger.warning("已拦截模型输出中的工具协议，未写入对话")
    if menu:
        return render_menu_reply(menu)
    if cleaned:
        return "我已根据已读取的信息整理好建议。请告诉我你想先确定哪一道菜。"
    return ""


async def _stream_final_reply(
    provider: LLMProvider,
    messages: list[dict[str, Any]],
    fallback: str,
    menu: dict[str, Any] | None,
) -> AsyncIterator[dict[str, Any]]:
    """最终文本独立生成；协议片段留在缓冲内，失败时撤回已发送文本。"""
    text = _safe_reply(fallback, menu)
    if not provider.supports_stream():
        yield {"type": "reply_ready", "text": text}
        return
    sent_chars = 0
    streamed_text = ""
    try:
        async for kind, delta in provider.chat_stream(messages=messages):
            if kind != "text" or not delta:
                continue
            streamed_text += delta
            if contains_protocol_leak(streamed_text):
                raise ValueError("模型流中检测到工具协议")
            safe_end = max(0, len(streamed_text) - 64)
            # '<' 可能是工具协议开头；不提前发出尚未闭合的标记。
            opening = streamed_text.rfind("<", 0, safe_end)
            if opening >= sent_chars and ">" not in streamed_text[opening:]:
                safe_end = opening
            if safe_end > sent_chars:
                yield {"type": "text_delta", "delta": streamed_text[sent_chars:safe_end]}
                sent_chars = safe_end
        if streamed_text:
            text = _safe_reply(streamed_text, menu)
            yield {"type": "text_delta", "delta": streamed_text[sent_chars:]}
    except Exception:  # 撤回不完整或泄漏协议的流，恢复已有安全回答。
        logger.warning("最终回答流式调用失败或包含协议，使用已有结果")
        if sent_chars:
            yield {"type": "text_reset"}
    yield {"type": "reply_ready", "text": text}

def _run_tool(name: str, args: dict[str, Any], context: PlanningContext, diner_ids: list[str] | None) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    if name == "get_member_constraints":
        rows = [r.summary for r in context.member_constraints if not diner_ids or not r.user_id or r.user_id in diner_ids]
        return {"constraints": rows, "count": len(rows)}, "未发现生效限制" if not rows else "；".join(rows), []
    if name == "get_inventory_summary":
        today = date.today(); items = [{"name": x.name, "quantity": str(x.quantity), "unit": x.unit, "expiring": x.key in context.expiring_keys(today)} for x in context.inventory]
        return {"items": items[:20], "total": len(items), "expiring": [x["name"] for x in items if x["expiring"]]}, f"{len(items)} 项可用库存", []
    if name == "search_recipes":
        query = str(args.get("query", "")).strip().lower()
        if not query: raise ValueError("缺少 query")
        matches = [r for r in context.recipes if query in " ".join([r.name, *r.tags, *r.taste_tags, *(x.name for x in r.ingredients)]).lower()]
        return {"query": query, "recipes": [{"id": r.id, "name": r.name, "duration_minutes": r.duration_minutes, "ingredients": [x.name for x in r.ingredients]} for r in matches[:8]], "total": len(matches)}, f"“{query}”命中 {len(matches)} 道菜谱", []
    if name in {"get_recipe_detail", "check_recipe_availability"}:
        recipe = _recipe(context, str(args.get("recipe", ""))); availability, suggestions = _availability(recipe, context, diner_ids)
        if name == "check_recipe_availability": return availability, f"{recipe.name}：{len(availability['missing_main_ingredients'])} 项非辅料缺项，{len(availability['pantry_staples'])} 项常备料", suggestions
        result = {"id": recipe.id, "name": recipe.name, "source": recipe.source, "servings": recipe.servings, "duration_minutes": recipe.duration_minutes, "ingredients": [{"key": x.key, "name": x.name, "quantity": str(x.quantity), "unit": x.unit, "optional": x.optional, "is_pantry_staple": x.key in context.pantry_keys} for x in recipe.ingredients], "steps": recipe.steps, "availability": availability}
        return result, f"已读取《{recipe.name}》的食材与 {len(recipe.steps)} 个步骤", suggestions
    if name == "get_preference_summary":
        rows = [{"dish": n, "rating": v.avg_rating, "count": v.sample_count, "tags": v.top_tags} for n, v in context.dish_feedback.items()]
        return {"preferences": rows[:10], "total": len(rows)}, "暂无历史偏好记录" if not rows else f"读取到 {len(rows)} 条历史偏好", []
    if name == "recommend_menu":
        result, suggestions = _recommend_menu(args, context, diner_ids)
        return result, f"已生成 {len(result['items'])} 道菜建议：{result['ready_count']} 道可直接做，{result['purchase_optional_count']} 道可选采购", suggestions
    raise ValueError(f"不允许调用工具：{name}")

def _trace(name: str, status: str, args: dict[str, Any], **extra: Any) -> ToolTraceView:
    label, template = TOOL_META.get(name, ("读取家庭数据", "执行查询"))
    try:
        summary = template.format(**args) if status == "running" else label
    except KeyError:  # 模型缺少展示参数时仍展示工具名称。
        summary = label
    return ToolTraceView(tool_name=name, status=status, input_summary=summary, **extra)

def _tool_error(exc: Exception) -> tuple[str, str]:
    """前端拿到可行动信息，服务端日志保留原始堆栈。"""
    if isinstance(exc, OperationalError):
        return ("数据库暂不可用或结构未升级；请执行数据库迁移后重启服务", "database_operational_error")
    return (f"工具执行失败：{type(exc).__name__}", "tool_execution_failed")

async def stream_conversation_agent(*, provider: LLMProvider | None, workspace_id: str, site_id: str, meal_service_id: str, diner_ids: list[str] | None, user_text: str, history: list[dict[str, str]], max_steps: int) -> AsyncIterator[dict[str, Any]]:
    if provider is None: yield {"type": "fallback"}; return
    context: PlanningContext | None = None; traces: list[ToolTraceView] = []; results: list[dict[str, Any]] = []; suggestions: dict[str, dict[str, Any]] = {}; menu_recommendation: dict[str, Any] | None = None
    preference_events: list[dict[str, str]] = []
    intent = await parse_meal_intent(provider, user_text, history)
    try:
        # 一轮对话只加载一次数据库快照；后续工具都使用同一份可信上下文，
        # 防止模型在不同工具间看到彼此不一致的库存、限制或菜谱数据。
        context = _load_context(workspace_id, site_id, meal_service_id)
    except Exception:  # 上下文加载失败时交还调用方的规则回答流程。
        yield {"type": "fallback"}
        return
    if intent is not None and intent.preference_updates:
        preference_args = {"updates": len(intent.preference_updates)}
        started = time.perf_counter()
        yield {
            "type": "tool",
            "trace": _trace(
                "upsert_member_preference",
                "running",
                preference_args,
                output_summary="正在写入",
            ),
        }
        preference_events = upsert_member_preferences(
            workspace_id, context, intent.preference_updates
        )
        saved_count = sum(event.get("status") == "saved" for event in preference_events)
        preference_trace = _trace(
            "upsert_member_preference",
            "success" if saved_count else "error",
            preference_args,
            duration_ms=max(1, round((time.perf_counter() - started) * 1000)),
            output_summary=(
                f"已写入 {saved_count} 条长期偏好"
                if saved_count
                else preference_events[0]["message"] if preference_events else "没有可写入的明确偏好"
            ),
        )
        traces.append(preference_trace)
        yield {"type": "tool", "trace": preference_trace}
        for event in preference_events:
            yield {"type": "preference_updated", "data": event}
        if any(event.get("status") == "saved" for event in preference_events):
            context = _load_context(workspace_id, site_id, meal_service_id)
    # 生成菜谱仅附加到本次内存中的 context，不写入数据库；下一轮仍以本地菜谱库为准。
    generated: list[RecipeContext] = []
    if intent is not None:
        diners = intent.diners or (len(diner_ids) if diner_ids else context.expected_diners)
        for dish_name in intent.requested_dishes:
            if find_recipe(context, dish_name) is not None:
                continue
            recipe = await generate_recipe(
                provider,
                dish_name,
                diners,
                context,
                requirements=_generation_requirements(
                    context,
                    diner_ids,
                    style=intent.style,
                    excluded_ingredients=intent.excluded_ingredients,
                ),
            )
            if recipe is not None and _accept_generated_recipe(
                recipe,
                context,
                diner_ids,
                excluded_ingredients=intent.excluded_ingredients,
            ):
                context.recipes.append(recipe)
                generated.append(recipe)
                yield {
                    "type": "recipe_generated",
                    "data": {
                        "recipe_id": recipe.id,
                        "name": recipe.name,
                        "source": recipe.source,
                    },
                }
    intent_payload = intent.model_dump(mode="json") if intent is not None else {}
    generated_payload = [
        {
            "name": recipe.name,
            "source": recipe.source,
            "servings": recipe.servings,
            "duration_minutes": recipe.duration_minutes,
            "ingredients": [
                {"name": line.name, "quantity": str(line.quantity), "unit": line.unit}
                for line in recipe.ingredients
            ],
            "steps": recipe.steps,
        }
        for recipe in generated
    ]
    authority = (
        "以下结构化意图由编排器校验，必须遵守；不得遗漏必选菜、重新加入排除菜或虚构来源："
        + json.dumps(intent_payload, ensure_ascii=False)
    )
    if generated_payload:
        authority += (
            "\n以下菜谱为模型生成而非本地库。回答中必须明确说明来源，不得称为本地菜谱："
            + json.dumps(generated_payload, ensure_ascii=False)
        )
    messages: list[dict[str, Any]] = [{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "system", "content": authority}, *history, {"role": "user", "content": user_text}]
    limit = max(1, min(max_steps, 8)); actions = 0
    if intent is not None and intent.task_type in {"meal_plan", "preference_update"}:
        menu_args: dict[str, Any] = {
            "required_dishes": intent.requested_dishes,
            "excluded_dishes": intent.excluded_dishes,
            "preferred_ingredients": intent.preferred_ingredients,
            "excluded_ingredients": intent.excluded_ingredients,
            "style": intent.style,
        }
        if intent.diners is not None:
            menu_args["diners"] = intent.diners
        if intent.budget_limit_cents is not None:
            menu_args["budget_limit_cents"] = intent.budget_limit_cents
        running = _trace("recommend_menu", "running", menu_args, output_summary="正在生成")
        yield {"type": "tool", "trace": running}
        started = time.perf_counter()
        result, summary, initial_suggestions = _run_tool(
            "recommend_menu", menu_args, context, diner_ids
        )
        # 整餐先严格使用本地菜谱编排，再依据“实际菜单结果”计算槽位缺口。
        # 最多补两道且只重新编排一次，避免模型生成触发无限递归或掩盖本地数据不足。
        generated_for_gaps = 0
        gap_roles = unfilled_menu_slots(menu_args, context, diner_ids, result)
        diners = int(result["diners"])
        for role in gap_roles[:2]:
            requirements = _generation_requirements(
                context,
                diner_ids,
                meal_role=role,
                style=intent.style,
                excluded_ingredients=intent.excluded_ingredients,
            )
            recipe = await generate_recipe(
                provider, None, diners, context, requirements=requirements
            )
            if recipe is None or not _accept_generated_recipe(
                recipe,
                context,
                diner_ids,
                expected_role=role,
                excluded_ingredients=intent.excluded_ingredients,
            ):
                continue
            if find_recipe(context, recipe.name) is not None:
                continue
            context.recipes.append(recipe)
            generated.append(recipe)
            generated_for_gaps += 1
            yield {
                "type": "recipe_generated",
                "data": {
                    "recipe_id": recipe.id,
                    "name": recipe.name,
                    "source": recipe.source,
                },
            }
        if generated_for_gaps:
            result, summary, initial_suggestions = _run_tool(
                "recommend_menu", menu_args, context, diner_ids
            )
            messages.append(
                {
                    "role": "system",
                    "content": "以下菜谱为模型生成而非本地库。回答中必须明确说明来源："
                    + json.dumps(
                        [
                            {
                                "name": recipe.name,
                                "source": recipe.source,
                                "servings": recipe.servings,
                            }
                            for recipe in generated[-generated_for_gaps:]
                        ],
                        ensure_ascii=False,
                    ),
                }
            )
        actions += 1
        menu_recommendation = result
        for item in initial_suggestions:
            suggestions.setdefault(_suggestion_key(item), item)
        done = _trace(
            "recommend_menu",
            "success",
            menu_args,
            duration_ms=max(1, round((time.perf_counter() - started) * 1000)),
            output_summary=summary,
        )
        traces.append(done)
        results.append({"tool_name": "recommend_menu", "status": "success", "summary": summary})
        yield {"type": "tool", "trace": done}
        messages.append(
            {
                "role": "system",
                "content": "这是确定性菜单结果，只能解释不能改菜："
                + json.dumps(result, ensure_ascii=False),
            }
        )
    tool_cache: dict[str, tuple[dict[str, Any], str, list[dict[str, Any]]]] = {}
    decision_rounds = 0
    while True:
        decision_rounds += 1
        if decision_rounds > limit + 1:
            text = _safe_reply("", menu_recommendation) or "我已复用本轮已读取的信息完成判断。请告诉我想优先调整哪一道菜。"
            yield {"type": "final", "turn": ConversationTurn(text, traces, results, list(suggestions.values()), menu_recommendation, preference_events)}
            return
        yield {"type": "status", "step": "agent_decide", "label": "判断下一步", "state": "running"}
        try:
            response = await provider.chat_with_tools(messages=messages, tools=TOOL_DEFINITIONS)
        except Exception:  # 模型不可用时交还调用方的规则回答流程。
            yield {"type": "fallback"}
            return
        response = normalize_model_message(response, _ALLOWED_TOOLS)
        yield {"type": "status", "step": "agent_decide", "label": "判断下一步", "state": "done", "detail": "已获得下一步决策"}
        calls = response["tool_calls"]
        if not calls:
            text = _safe_reply(str(response.get("content") or ""), menu_recommendation)
            final_messages = [*messages, {
                "role": "system",
                "content": "依据已有工具结果直接输出最终自然语言回答，不要调用工具或输出协议。保持用户要求、已选菜品与份量不变。",
            }]
            yield {"type": "status", "step": "compose_reply", "label": "正在组织回答", "state": "running"}
            async for event in _stream_final_reply(provider, final_messages, text, menu_recommendation):
                if event["type"] == "reply_ready":
                    text = event["text"]
                else:
                    yield event
            yield {"type": "final", "turn": ConversationTurn(text, traces, results, list(suggestions.values()), menu_recommendation, preference_events)} if text else {"type": "fallback"}; return
        assistant = {"role": response.get("role") or "assistant", "content": response.get("content") or "", "tool_calls": calls}
        if response.get("reasoning_content"): assistant["reasoning_content"] = response["reasoning_content"]
        messages.append(assistant)
        for call in calls:
            fn = call["function"]
            name = fn["name"]
            args = json.loads(fn["arguments"])
            if name == "recommend_menu" and intent is not None:
                if intent.diners is not None:
                    args["diners"] = intent.diners
                if intent.budget_limit_cents is not None:
                    args["budget_limit_cents"] = intent.budget_limit_cents
                args["required_dishes"] = intent.requested_dishes
                args["excluded_dishes"] = intent.excluded_dishes
                args["preferred_ingredients"] = intent.preferred_ingredients
                args["excluded_ingredients"] = intent.excluded_ingredients
                if intent.style:
                    args["style"] = intent.style
            fingerprint = f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
            if fingerprint in tool_cache:
                result, summary, new = tool_cache[fingerprint]
                done = _trace(name, "success", args, duration_ms=0, output_summary=f"已复用本轮结果：{summary}")
            elif actions >= limit:
                result = {"error": "工具总额度已用尽，请依据已有结果作答"}; done = _trace(name, "error", args, output_summary=result["error"], error_code="tool_limit_reached")
            else:
                yield {"type": "tool", "trace": _trace(name, "running", args, output_summary="正在读取")}
                started = time.perf_counter(); actions += 1
                try:
                    result, summary, new = _run_tool(name, args, context, diner_ids)
                    for item in new:
                        suggestions.setdefault(_suggestion_key(item), item)
                    tool_cache[fingerprint] = (result, summary, new)
                    if name == "recommend_menu": menu_recommendation = result
                    done = _trace(name, "success", args, duration_ms=max(1, round((time.perf_counter()-started)*1000)), output_summary=summary)
                except Exception as exc:  # 将工具执行失败转换为可展示的 trace 和模型错误结果。
                    logger.exception("对话工具执行失败：%s", name)
                    message, error_code = _tool_error(exc)
                    result = {"error": message}; done = _trace(name, "error", args, duration_ms=max(1, round((time.perf_counter()-started)*1000)), output_summary=message, error_code=error_code)
            traces.append(done); results.append({"tool_name": name, "status": done.status, "summary": done.output_summary}); yield {"type": "tool", "trace": done}
            messages.append({"role": "tool", "tool_call_id": str(call.get("id") or name), "content": json.dumps(result, ensure_ascii=False)})
        if actions >= limit:
            text = _safe_reply("", menu_recommendation) or "我已完成本轮查询。为避免重复查询，请告诉我你想优先决定哪一点。"
            final_messages = [*messages, {"role": "system", "content": _WRAP_UP_PROMPT}]
            yield {"type": "status", "step": "compose_reply", "label": "正在组织回答", "state": "running"}
            if provider.supports_stream():
                async for event in _stream_final_reply(provider, final_messages, text, menu_recommendation):
                    if event["type"] == "reply_ready":
                        text = event["text"]
                    else:
                        yield event
                yield {"type": "final", "turn": ConversationTurn(text, traces, results, list(suggestions.values()), menu_recommendation, preference_events)}
                return
            try:
                raw = await provider.chat(messages=[*messages, {"role": "system", "content": _WRAP_UP_PROMPT}])
                text = _safe_reply(raw, menu_recommendation)
                if text:
                    yield {"type": "final", "turn": ConversationTurn(text, traces, results, list(suggestions.values()), menu_recommendation, preference_events)}
                    return
            except Exception:  # 收束调用失败时仍返回已有菜单结果。
                logger.warning("收束回答失败，改用已有菜单结果")
            fallback = _safe_reply("", menu_recommendation) or "我已完成本轮查询。为避免重复查询，请告诉我你想优先决定哪一点。"
            yield {"type": "final", "turn": ConversationTurn(fallback, traces, results, list(suggestions.values()), menu_recommendation, preference_events)}
            return

async def run_conversation_agent(**kwargs: Any) -> ConversationTurn | None:
    callback: Callable[[ToolTraceView], None] | None = kwargs.pop("on_tool", None)
    async for event in stream_conversation_agent(**kwargs):
        if event["type"] == "tool" and event["trace"].status != "running" and callback: callback(event["trace"])
        if event["type"] == "final": return event["turn"]
        if event["type"] == "fallback": return None
    return None
