"""Agent 服务：约束解析、工具编排、三方案生成、What-if 重规划、运行记录落库。

当前约束解析为规则基线（模型超时/失败时的同一降级路径）；配置 LLM_API_KEY 后
由 LLMProvider 替换解析节点，HTTP 契约不变（技术开发文档 5.2、6）。
"""

import json
import re
import time
import uuid
from datetime import date

from app.core.config import get_settings
from app.agent.graph import build_meal_planning_graph
from app.agent.state import MealPlanningState
from app.db.models import AgentRun, MealService, ToolTrace
from app.db.session import SessionLocal
from app.llm.factory import build_llm_provider
from app.llm.provider import LLMProvider
from app.schemas.agent import (
    AgentRunView,
    MenuOption,
    PlanningConstraints,
    RunStatus,
    ToolTraceView,
)
from app.services import planning
from app.services.context_loader import (
    PlanningContext,
    ScopeNotFoundError,
    load_planning_context,
)


def _extract_constraints(text: str, context: PlanningContext) -> PlanningConstraints:
    diner_match = re.search(r"(\d+)\s*人", text)
    budget_match = re.search(r"预算\s*(\d+)\s*元", text)
    preferred_dishes = [recipe.name for recipe in context.recipes if recipe.name in text]
    if "焖面" in text and not any("焖面" in name for name in preferred_dishes):
        preferred_dishes.append("豆角焖面")
    return PlanningConstraints(
        expected_diners=int(diner_match.group(1)) if diner_match else context.expected_diners,
        budget_limit_cents=(
            int(budget_match.group(1)) * 100 if budget_match else context.budget_limit_cents
        ),
        dietary_style=["清淡"] if "清淡" in text else [],
        excluded_ingredients=["花生"] if "花生" in text else [],
        preferred_dishes=preferred_dishes,
        prioritize_expiring="临期" in text or "到期" in text,
        time_limit_minutes=90,
    )


def _constraints_summary(constraints: PlanningConstraints) -> str:
    budget = (
        f"{constraints.budget_limit_cents / 100:.0f} 元"
        if constraints.budget_limit_cents
        else "未指定"
    )
    return (
        f"人数 {constraints.expected_diners or '未指定'}，预算 {budget}，"
        f"风格 {'、'.join(constraints.dietary_style) or '无'}，"
        f"排除 {'、'.join(constraints.excluded_ingredients) or '无'}，"
        f"点名菜品 {'、'.join(constraints.preferred_dishes) or '无'}，"
        f"临期优先 {'是' if constraints.prioritize_expiring else '否'}"
    )


_CONSTRAINT_SYSTEM_PROMPT = (
    "你是家庭厨房配餐 Agent 的约束解析器。从用户需求中抽取结构化约束，"
    "只输出一个 JSON 对象，不要输出解释或代码块标记。字段定义："
    '{"expected_diners": 用餐人数整数或null, '
    '"budget_limit_cents": 预算总金额（元乘以100）整数或null, '
    '"dietary_style": 口味风格标签数组如["清淡"], '
    '"excluded_ingredients": 排除食材名称数组, '
    '"preferred_dishes": 用户明确想吃、指定或点名的菜品数组，如["豆角焖面"], '
    '"prioritize_expiring": 是否优先使用临期食材的布尔值（必须为true或false，不能为null）, '
    '"time_limit_minutes": 出餐时长上限整数或null}。'
    "无法确定的可空字段填 null，数组字段填空数组；prioritize_expiring 无法确定时填 false。"
    "金额必须换算为分。"
)


def _strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1] if "```" in cleaned else cleaned
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    return cleaned.strip()


async def _parse_constraints_with_llm(
    provider: LLMProvider | None,
    request_text: str,
    context: PlanningContext,
    on_thinking=None,
) -> tuple[PlanningConstraints, ToolTraceView, bool]:
    """返回 (约束, 解析轨迹, 是否成功使用模型)。模型不可用/失败时降级规则基线。"""
    settings = get_settings()
    if provider is None:
        return (
            _extract_constraints(request_text, context),
            ToolTraceView(
                tool_name="parse_request",
                status="skipped",
                duration_ms=0,
                input_summary="规则基线（未配置云端模型）",
                output_summary="由规则引擎完成约束解析",
            ),
            False,
        )

    started = time.perf_counter()
    try:
        parse_messages = [
            {"role": "system", "content": _CONSTRAINT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"餐次上下文：{context.service_date} {context.meal_service_name}，"
                    f"默认 {context.expected_diners} 人。"
                    f"用户需求：{request_text}"
                ),
            },
        ]
        raw = ""
        # 按 Provider 契约判断流式能力，基类默认不支持流式。
        use_stream = provider.supports_stream()
        if use_stream:
            # 思维链直播:模型推理增量实时回调,约束 JSON 从正文增量中累积
            text_parts: list[str] = []
            async for kind, delta in provider.chat_stream(messages=parse_messages):
                if kind == "thinking" and on_thinking:
                    on_thinking(delta)
                elif kind == "text":
                    text_parts.append(delta)
            raw = "".join(text_parts)
        if not raw:
            raw = await provider.chat(messages=parse_messages)
        duration_ms = round((time.perf_counter() - started) * 1000)
        data = json.loads(_strip_code_fence(raw))
        if data.get("budget_limit_cents") is None and data.get("budget_yuan") is not None:
            data["budget_limit_cents"] = int(data.pop("budget_yuan")) * 100
        # 云端模型偶尔会把未提及的布尔值返回为 null；业务默认值应为 False，
        # 不能因为一个可安全归一化的字段让整条 Agent 链路降级。
        if data.get("prioritize_expiring") is None:
            data["prioritize_expiring"] = False
        if data.get("dietary_style") is None:
            data["dietary_style"] = []
        if data.get("excluded_ingredients") is None:
            data["excluded_ingredients"] = []
        if data.get("preferred_dishes") is None:
            data["preferred_dishes"] = []
        constraints = PlanningConstraints(**data)
        trace = ToolTraceView(
            tool_name="parse_request",
            status="success",
            duration_ms=duration_ms,
            input_summary=f"LLM 约束解析（{settings.llm_provider}/{settings.llm_model}）",
            output_summary=_constraints_summary(constraints),
        )
        return constraints, trace, True
    except Exception as exc:  # 模型失败必须降级，不阻断核心计算
        duration_ms = round((time.perf_counter() - started) * 1000)
        constraints = _extract_constraints(request_text, context)
        trace = ToolTraceView(
            tool_name="parse_request",
            status="fallback",
            duration_ms=duration_ms,
            input_summary=f"LLM 约束解析（{settings.llm_provider}/{settings.llm_model}）",
            output_summary=f"模型解析失败，已降级规则基线：{type(exc).__name__}: {str(exc)[:120]}",
            error_code="llm_parse_failed",
        )
        return constraints, trace, False


def _restriction_chips(context: PlanningContext, key_names: dict[str, str]) -> list[str]:
    """家庭档案预置的用餐限制（allergy/dislike），与本次输入无关，用于 UI 明示来源。"""
    chips = []
    for key, kind in context.restriction_types.items():
        label = "过敏原" if kind == "allergy" else "忌口"
        chips.append(f"{label}：{key_names.get(key, key)}")
    return chips


def _plan(
    context: PlanningContext,
    constraints: PlanningConstraints,
    parse_trace: ToolTraceView,
    on_step=None,
) -> tuple[list[ToolTraceView], list[MenuOption], list[str], list[str]]:
    """on_step(state, tool_name, detail):每个工具开始/结束时实时回调,SSE 直播用。"""
    diners = constraints.expected_diners or context.expected_diners
    traces: list[ToolTraceView] = [parse_trace]

    def _timed_tool(name: str, input_summary: str, produce_output):
        if on_step:
            on_step("running", name, input_summary)
        started = time.perf_counter()
        result = produce_output()
        duration_ms = round((time.perf_counter() - started) * 1000)
        if isinstance(result, tuple) and len(result) == 2:
            display, payload = result
        else:
            display, payload = result, None
        if on_step:
            on_step("done", name, str(display))
        return (
            ToolTraceView(
                tool_name=name,
                status="success",
                duration_ms=duration_ms,
                input_summary=input_summary,
                output_summary=str(display),
            ),
            payload,
        )

    trace, _ = _timed_tool(
        "get_meal_service_context",
        "已校验业务作用域",
        lambda: f"{context.workspace_name}·{context.site_name} "
        f"{context.service_date} {context.meal_service_name} 预计 {diners} 人",
    )
    traces.append(trace)

    def _inventory_summary() -> str:
        expiring_keys = context.expiring_keys(date.today())
        expiring_names = "、".join(
            line.name for line in context.inventory if line.key in expiring_keys
        )
        return f"{len(context.inventory)} 项库存，临期 {len(expiring_keys)} 项（{expiring_names}）"

    trace, _ = _timed_tool("list_inventory", "读取可用库存", _inventory_summary)
    traces.append(trace)

    def _member_constraints_summary() -> str:
        """约束前置:汇总成员永久忌口/过敏与当日生效的特殊事件。"""
        if not context.member_constraints:
            return "全家当前没有生效的忌口或特殊事件"
        return "；".join(constraint.summary for constraint in context.member_constraints)

    trace, _ = _timed_tool("get_member_constraints", "检查用餐限制", _member_constraints_summary)
    traces.append(trace)

    trace, _ = _timed_tool(
        "search_recipe_candidates", "全量召回菜谱", lambda: f"召回 {len(context.recipes)} 道候选菜"
    )
    traces.append(trace)

    key_names = planning.build_key_names(context)
    workspace_restrictions = _restriction_chips(context, key_names)

    def _hard_filter():
        kept, filtered_out = planning.hard_filter(
            context.recipes, context.excluded_keys, key_names, context.restriction_types
        )
        detail = "；".join(f"{name}（{reason}）" for name, reason in filtered_out) or "无"
        return f"保留 {len(kept)} 道，过滤 {len(filtered_out)} 道：{detail}", (kept, filtered_out)

    trace, (kept, filtered_out) = _timed_tool(
        "filter_hard_constraints",
        f"家庭档案限制：{'、'.join(workspace_restrictions) or '无'}",
        _hard_filter,
    )
    traces.append(trace)

    expiring_keys = context.expiring_keys(context.service_date)

    def _score_and_build():
        scored = planning.score_candidates(kept, context, constraints, expiring_keys)
        options = planning.build_all_options(
            scored, context, constraints, filtered_out, key_names
        )
        summary = "、".join(
            f"{option.title}（{option.score_breakdown['total']}分）" for option in options
        )
        return f"三方案生成：{summary}", options

    trace, options = _timed_tool(
        "score_menu_options", f"候选 {len(kept)} 道，三策略加权", _score_and_build
    )
    traces.append(trace)

    expiring_first = next(option for option in options if option.strategy == "expiring_first")
    chosen_names = {item["name"] for item in expiring_first.items}
    chosen_recipes = [recipe for recipe in kept if recipe.name in chosen_names]

    def _scale():
        requirements = planning.compute_requirements(chosen_recipes, diners)
        return (
            f"按 {diners} 人换算 {len(chosen_recipes)} 道菜共 {len(requirements)} 项食材用量",
            requirements,
        )

    trace, requirements = _timed_tool(
        "scale_recipe_quantities",
        f"基准份数 → {diners} 人",
        _scale,
    )
    traces.append(trace)

    def _estimate() -> str:
        total_cost, total_kcal = planning.estimate_totals(chosen_recipes, diners)
        return (
            f"按“{expiring_first.title}”估算：人均约 {total_kcal} kcal，"
            f"总成本约 {total_cost / 100:.0f} 元"
        )

    trace, _ = _timed_tool("estimate_nutrition_and_cost", "热量与成本估算", _estimate)
    traces.append(trace)

    def _procurement() -> str:
        lines = planning.procurement_lines(requirements, context, key_names)
        detail = "、".join(
            f"{line['ingredient']} 缺 {line['missing_quantity']}" for line in lines[:5]
        )
        return f"{len(lines)} 项采购缺口：{detail}"

    trace, _ = _timed_tool("build_procurement_list", "比对库存计算缺口", _procurement)
    traces.append(trace)

    warnings: list[str] = []
    if parse_trace.status == "skipped":
        warnings.append("未配置云端模型（LLM_API_KEY 为空），约束解析由规则基线完成")
    elif parse_trace.status == "fallback":
        warnings.append("云端模型解析失败，约束解析已降级规则基线（详见工具轨迹 parse_request）")

    return traces, options, warnings, workspace_restrictions


def _save_run(run: AgentRunView, request_text: str, scope: tuple[str, str]) -> None:
    workspace_id, meal_service_id = scope
    state = {
        "options": [option.model_dump() for option in run.options],
        "tool_trace": [trace.model_dump() for trace in run.tool_trace],
        "warnings": run.warnings,
        "approval_status": run.approval_status,
        "workspace_restrictions": run.workspace_restrictions,
    }
    with SessionLocal() as session:
        row = session.get(AgentRun, run.id)
        if row is None:
            row = AgentRun(
                id=run.id,
                workspace_id=workspace_id,
                meal_service_id=meal_service_id,
                request_text=request_text,
            )
            session.add(row)
        row.constraints = run.constraints.model_dump()
        row.state = state
        row.status = run.status.value
        row.provider = run.provider
        row.model = run.model
        session.query(ToolTrace).filter(ToolTrace.agent_run_id == run.id).delete()
        for trace in run.tool_trace:
            session.add(
                ToolTrace(
                    agent_run_id=run.id,
                    tool_name=trace.tool_name,
                    status=trace.status,
                    duration_ms=trace.duration_ms,
                    input_summary=trace.input_summary,
                    output_summary=trace.output_summary,
                    error_code=trace.error_code,
                )
            )
        session.commit()


def _to_view(row: AgentRun) -> AgentRunView:
    state = row.state
    return AgentRunView(
        id=row.id,
        status=RunStatus(row.status),
        constraints=PlanningConstraints(**row.constraints),
        tool_trace=[ToolTraceView(**trace) for trace in state.get("tool_trace", [])],
        options=[MenuOption(**option) for option in state.get("options", [])],
        warnings=state.get("warnings", []),
        workspace_restrictions=state.get("workspace_restrictions", []),
        approval_status=state.get("approval_status", "not_requested"),
        provider=row.provider,
        model=row.model,
    )


async def create_run(
    workspace_id: str,
    site_id: str,
    meal_service_id: str,
    request_text: str,
    on_step=None,
    on_thinking=None,
) -> AgentRunView:
    with SessionLocal() as session:
        context = load_planning_context(session, workspace_id, site_id, meal_service_id)

    provider = build_llm_provider(get_settings())

    # 编排交给 LangGraph 状态图(app/agent/graph.py):节点体复用本模块的
    # 约束解析与 planning.py 纯函数链,行为与原顺序编排逐行等价。
    graph = build_meal_planning_graph()
    initial_state: MealPlanningState = {
        "workspace_id": workspace_id,
        "site_id": site_id,
        "meal_service_id": meal_service_id,
        "request_text": request_text,
        "context": context,
        "provider": provider,
        "on_step": on_step,
        "on_thinking": on_thinking,
    }
    final_state = await graph.ainvoke(initial_state)

    traces = final_state["tool_trace"]
    options = final_state["menu_options"]
    warnings = final_state["warnings"]
    workspace_restrictions = final_state["workspace_restrictions"]
    used_llm = final_state["parse_used_llm"]

    settings = get_settings()
    run = AgentRunView(
        id=str(uuid.uuid4()),
        status=RunStatus.WAITING_APPROVAL,
        constraints=final_state["constraints"],
        tool_trace=traces,
        options=options,
        warnings=warnings,
        workspace_restrictions=workspace_restrictions,
        approval_status="waiting",
        provider=settings.llm_provider if used_llm else None,
        model=settings.llm_model if used_llm else None,
    )
    _save_run(run, request_text, scope=(workspace_id, meal_service_id))
    return run


def get_run(run_id: str) -> AgentRunView | None:
    with SessionLocal() as session:
        row = session.get(AgentRun, run_id)
        if row is None:
            return None
        return _to_view(row)


def get_latest_run() -> AgentRunView | None:
    with SessionLocal() as session:
        row = session.query(AgentRun).order_by(AgentRun.created_at.desc()).first()
        if row is None:
            return None
        return _to_view(row)


async def revise_run(
    run_id: str, expected_diners: int | None, budget_limit_cents: int | None
) -> AgentRunView | None:
    with SessionLocal() as session:
        row = session.get(AgentRun, run_id)
        if row is None:
            return None
        view = _to_view(row)
        scope = (row.workspace_id, row.meal_service_id)

    with SessionLocal() as session:
        meal_service = session.get(MealService, scope[1])
        site_id = meal_service.site_id if meal_service else ""
        context = load_planning_context(session, scope[0], site_id, scope[1])

    # What-if 重规划沿用创建时已解析的约束（保留 LLM 解析结果），只覆盖显式修改的字段
    constraints = view.constraints.model_copy(deep=True)
    if expected_diners is not None:
        constraints.expected_diners = expected_diners
    if budget_limit_cents is not None:
        constraints.budget_limit_cents = budget_limit_cents

    parse_trace = ToolTraceView(
        tool_name="parse_request",
        status="success",
        duration_ms=0,
        input_summary="What-if 重规划",
        output_summary=_constraints_summary(constraints),
    )
    traces, options, warnings, workspace_restrictions = _plan(context, constraints, parse_trace)

    revised = view.model_copy(deep=True)
    revised.constraints = constraints
    revised.options = options
    revised.tool_trace = view.tool_trace + traces
    revised.warnings = warnings
    revised.workspace_restrictions = workspace_restrictions
    revised.status = RunStatus.WAITING_APPROVAL
    revised.approval_status = "waiting"
    _save_run(revised, row.request_text, scope)
    return revised


__all__ = ["ScopeNotFoundError", "create_run", "get_run", "revise_run"]
