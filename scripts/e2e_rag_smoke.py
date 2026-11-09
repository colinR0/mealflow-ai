"""一次性端到端联调：真实数据库 + 真实 DeepSeek + 真实 BGE 检索，跑一次 agent 问菜谱。

用法（在项目根目录，环境变量由外部设置好 DATABASE_URL/RETRIEVAL_*）：
    D:\APP\miniconda3\envs\mealflow\python.exe scripts/_e2e_rag_smoke.py "番茄和牛肉怎么做"
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sqlalchemy import select

from app import worker
from app.agent import durable_graph as graph
from app.db.session import SessionLocal
from app.db.models import User, Workspace, WorkspaceMember
from app.db.v2_models import TurnRun
from app.llm.durable_provider import ConfiguredProvider
from app.services import v2_runtime as runtime


def ensure_actor():
    """确保数据库里有一个 user + household，返回 (user_id, workspace_id)。"""
    with SessionLocal() as session:
        member = session.scalar(select(WorkspaceMember))
        if member:
            return member.user_id, member.workspace_id
        user = User(display_name="e2e", password_hash="unused")
        household = Workspace(name="e2e", kind="personal")
        session.add_all([user, household])
        session.flush()
        session.add(WorkspaceMember(workspace_id=household.id, user_id=user.id,
                                    role="user", status="active"))
        session.commit()
        return user.id, household.id


def main() -> int:
    message = sys.argv[1] if len(sys.argv) > 1 else "番茄和牛肉怎么做"
    actor = ensure_actor()

    # 模拟 worker 的正常启动路径：create_turn -> claim -> snapshot
    response = runtime.create_turn(*actor, None, message)
    job_id = worker.claim("graph-worker")
    snapshot = worker.snapshot_job(job_id, "graph-worker")

    print(f"[端到端] 提问: {message}")
    print(f"[端到端] run_id: {response['run_id']}")

    provider = ConfiguredProvider()
    result = graph.compute(SessionLocal, snapshot, provider=provider)

    print("=" * 60)
    print(f"状态: {result.get('status')}")
    if result.get("text"):
        print(f"回答:\n{result['text']}")
    if result.get("citations"):
        print(f"引用: {result['citations']}")
    if result.get("evidence"):
        print(f"证据条数: {len(result['evidence'])}")
        for ev in result["evidence"][:3]:
            print(f"  - {ev.get('id')} | {ev.get('content', {}).get('name', '')}")
    if result.get("warnings"):
        print(f"警告: {result['warnings']}")
    if result.get("error_code"):
        print(f"错误码: {result['error_code']}")

    # 落库
    worker.finish(snapshot, result)
    with SessionLocal() as session:
        saved = session.get(TurnRun, response["run_id"])
        print(f"[端到端] 落库状态: {saved.status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
