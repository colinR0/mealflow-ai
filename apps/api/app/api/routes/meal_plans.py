"""菜单计划路由（docs/api.md：POST /meal-plans、POST /meal-plans/{id}/publish）。"""

from fastapi import APIRouter, Header, HTTPException, status

from app.schemas.meal_plan import MealPlanCreate, MealPlanView
from app.services import meal_plan_service
from app.services.meal_plan_service import (
    ApprovalRequiredError,
    IdempotencyKeyRequiredError,
    PlanAlreadyPublishedError,
    PlanNotFoundError,
    StrategyNotFoundError,
)

router = APIRouter(prefix="/meal-plans", tags=["meal-plans"])


@router.post("", response_model=MealPlanView, status_code=status.HTTP_201_CREATED)
def confirm_meal_plan(payload: MealPlanCreate) -> MealPlanView:
    """管理员确认选中方案：保存菜单计划并记录确认状态，不产生发布副作用。"""
    try:
        return meal_plan_service.confirm_plan(payload.agent_run_id, payload.strategy)
    except PlanNotFoundError as exc:  # Translate missing plan/run to HTTP 404.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except StrategyNotFoundError as exc:  # Report an invalid strategy as a client input error.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{plan_id}", response_model=MealPlanView)
def get_meal_plan(plan_id: str) -> MealPlanView:
    plan = meal_plan_service.get_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="菜单计划不存在")
    return plan


@router.post("/{plan_id}/publish", response_model=MealPlanView)
def publish_meal_plan(
    plan_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> MealPlanView:
    """发布菜单：要求管理员已确认，且使用 Idempotency-Key 防重复发布。"""
    try:
        return meal_plan_service.publish_plan(plan_id, idempotency_key)
    except IdempotencyKeyRequiredError as exc:  # Report the missing required request header.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PlanNotFoundError as exc:  # Translate missing plan/run to HTTP 404.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (ApprovalRequiredError, PlanAlreadyPublishedError) as exc:  # Map state conflict to 409.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
