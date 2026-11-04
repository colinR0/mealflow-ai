"""菜单计划服务：管理员确认方案 → 保存菜单 → 发布（api.md 契约）。

发布是高风险副作用（技术开发文档 5.2）：必须存在 Agent run、选中方案与
管理员确认状态，并使用 Idempotency-Key 保证同键重放返回同一结果。
"""

from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AgentRun, AgentRunStatus, MealPlan, MealService, Recipe
from app.db.session import SessionLocal
from app.schemas.agent import MenuOption
from app.schemas.meal_plan import MealPlanView


class PlanNotFoundError(LookupError):
    """菜单计划或其引用的 Agent run 不存在。"""


class StrategyNotFoundError(ValueError):
    """请求确认的策略不在该运行的三个方案中。"""


class ApprovalRequiredError(RuntimeError):
    """发布前缺少管理员确认状态。"""


class PlanAlreadyPublishedError(RuntimeError):
    """菜单已用其他幂等键发布过。"""


class IdempotencyKeyRequiredError(ValueError):
    """发布请求缺少 Idempotency-Key。"""


def _to_view(plan: MealPlan) -> MealPlanView:
    return MealPlanView(
        id=plan.id,
        workspace_id=plan.workspace_id,
        meal_service_id=plan.meal_service_id,
        agent_run_id=plan.agent_run_id,
        strategy=plan.strategy,
        status=plan.status,
        items=plan.items,
        procurement_list=plan.procurement_list,
        estimated_cost_cents=plan.estimated_cost_cents,
        constraints_snapshot=plan.constraints_snapshot,
        published_at=plan.published_at,
    )


def _load_run(session: Session, run_id: str) -> AgentRun:
    run = session.get(AgentRun, run_id)
    if run is None:
        raise PlanNotFoundError(f"agent run {run_id} 不存在")
    return run


def _option_for(run: AgentRun, strategy: str) -> MenuOption:
    options = [MenuOption(**option) for option in run.state.get("options", [])]
    for option in options:
        if option.strategy == strategy:
            return option
    raise StrategyNotFoundError(f"方案 {strategy} 不在该运行的候选中")


def _items_for_option(option: MenuOption, session: Session) -> list[dict]:
    """按数据字典落盘菜单项：recipe_id、份数、热量与生成时的评分拆解快照。"""
    names = [item["name"] for item in option.items]
    recipes = {
        recipe.name: recipe
        for recipe in session.scalars(select(Recipe).where(Recipe.name.in_(names))).all()
    }
    return [
        {
            "name": item["name"],
            "recipe_id": recipes[item["name"]].id if item["name"] in recipes else None,
            "servings": item["servings"],
            "estimated_kcal_per_serving": item["kcal_per_serving"],
            "score_breakdown": option.score_breakdown,
        }
        for item in option.items
    ]


def _mark_run_approved(session: Session, run: AgentRun) -> None:
    state = dict(run.state)
    state["approval_status"] = "approved"
    run.state = state
    session.add(run)


def confirm_plan(agent_run_id: str, strategy: str) -> MealPlanView:
    """管理员确认选中方案：保存菜单计划并记录确认状态（无发布副作用）。"""
    with SessionLocal() as session:
        existing = session.scalar(
            select(MealPlan).where(
                MealPlan.agent_run_id == agent_run_id, MealPlan.strategy == strategy
            )
        )
        if existing is not None:
            return _to_view(existing)  # 重复确认返回同一计划

        run = _load_run(session, agent_run_id)
        option = _option_for(run, strategy)
        plan = MealPlan(
            workspace_id=run.workspace_id,
            meal_service_id=run.meal_service_id,
            agent_run_id=run.id,
            strategy=strategy,
            items=_items_for_option(option, session),
            procurement_list=option.procurement_list,
            estimated_cost_cents=option.estimated_cost_cents,
            constraints_snapshot=run.constraints,
        )
        session.add(plan)
        _mark_run_approved(session, run)
        session.commit()
        session.refresh(plan)
        return _to_view(plan)


def publish_plan(plan_id: str, idempotency_key: str | None) -> MealPlanView:
    """发布菜单：餐次与计划置为 published，运行状态置为 PUBLISHED。"""
    if not idempotency_key:
        raise IdempotencyKeyRequiredError("发布请求必须携带 Idempotency-Key")

    with SessionLocal() as session:
        plan = session.get(MealPlan, plan_id)
        if plan is None:
            raise PlanNotFoundError(f"meal plan {plan_id} 不存在")

        if plan.publish_idempotency_key == idempotency_key:
            return _to_view(plan)  # 幂等重放：返回同一已发布结果
        if plan.status == "published":
            raise PlanAlreadyPublishedError("该菜单已发布，不能使用新的幂等键重复发布")

        run = _load_run(session, plan.agent_run_id)
        state = run.state
        if state.get("approval_status") != "approved":
            raise ApprovalRequiredError("缺少管理员确认状态，不能发布")

        now = datetime.now(timezone.utc)
        plan.status = "published"
        plan.published_at = now
        plan.publish_idempotency_key = idempotency_key
        session.add(plan)

        meal_service = session.get(MealService, plan.meal_service_id)
        if meal_service is not None:
            meal_service.status = "published"
            session.add(meal_service)

        run.status = AgentRunStatus.PUBLISHED.value
        run.state = {**state, "approval_status": "approved"}
        session.add(run)

        session.commit()
        session.refresh(plan)
        return _to_view(plan)


def get_plan(plan_id: str) -> MealPlanView | None:
    with SessionLocal() as session:
        plan = session.get(MealPlan, plan_id)
        if plan is None:
            return None
        return _to_view(plan)


def latest_published_plan_for_site(session: Session, site_id: str) -> MealPlan | None:
    """设备绑定站点的最新已发布菜单（Kiosk 读取入口）。"""
    return session.execute(
        select(MealPlan)
        .join(MealService, MealService.id == MealPlan.meal_service_id)
        .where(MealService.site_id == site_id, MealPlan.status == "published")
        .order_by(MealPlan.published_at.desc(), MealPlan.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


__all__ = [    "ApprovalRequiredError",
    "IdempotencyKeyRequiredError",
    "PlanAlreadyPublishedError",
    "PlanNotFoundError",
    "StrategyNotFoundError",
    "confirm_plan",
    "get_plan",
    "latest_published_plan_for_site",
    "publish_plan",
]
