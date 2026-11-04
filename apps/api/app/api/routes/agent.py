from fastapi import APIRouter, HTTPException, status

from app.schemas.agent import (
    AgentRunView,
    CreateAgentRunRequest,
    ReviseAgentRunRequest,
)
from app.services.agent_service import (
    ScopeNotFoundError,
    create_run,
    get_latest_run,
    get_run,
    revise_run,
)

router = APIRouter(prefix="/agent/runs", tags=["agent"])


@router.post("", response_model=AgentRunView, status_code=status.HTTP_201_CREATED)
async def create_agent_run(payload: CreateAgentRunRequest) -> AgentRunView:
    # 约束解析优先走云端模型，失败自动降级规则基线；HTTP 契约保持不变。
    try:
        return await create_run(
            payload.workspace_id, payload.site_id, payload.meal_service_id, payload.request_text
        )
    except ScopeNotFoundError as exc:  # Translate invalid scope to an HTTP input error.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# 固定路径必须先于 /{run_id} 注册，否则 "latest" 会被当作运行 ID 匹配
@router.get("/latest", response_model=AgentRunView)
def get_latest_agent_run() -> AgentRunView:
    run = get_latest_run()
    if run is None:
        raise HTTPException(status_code=404, detail="No agent runs yet")
    return run


@router.get("/{run_id}", response_model=AgentRunView)
def get_agent_run(run_id: str) -> AgentRunView:
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return run


@router.post("/{run_id}/revise", response_model=AgentRunView)
async def revise_agent_run(run_id: str, payload: ReviseAgentRunRequest) -> AgentRunView:
    run = await revise_run(run_id, payload.expected_diners, payload.budget_limit_cents)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return run
