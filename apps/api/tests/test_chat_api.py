import json
from datetime import date

import httpx
import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.services.chat_service import (
    _DishTagFilter,
    _constraint_warnings_for_text,
    _dish_name_from_text,
    _extract_diners,
    _fallback_general_text,
    _fallback_route,
    _filter_constraints,
    _format_warning_block,
    _looks_like_inventory_menu,
    _normalize_request_text,
    _wants_non_library_recipe,
)
from app.services.context_loader import InventoryLine, MemberConstraint, PlanningContext


@pytest.fixture(autouse=True)
def _disable_web_recipe_search(monkeypatch):
    """单元测试不打真实外网:库未命中场景统一走 LLM 通用做法兜底路径。"""
    monkeypatch.setattr(
        "app.services.web_recipe_search.search_with_briefs", lambda *args, **kwargs: []
    )


def _parse_sse_blocks(blocks: list[str]) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in blocks:
        name = None
        data: dict = {}
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
        if name is not None:
            events.append((name, data))
    return events


async def _stream(
    client: AsyncClient, message: str, session_id: str | None = None
) -> list[tuple[str, dict]]:
    blocks: list[str] = []
    buffer = ""
    payload: dict = {"message": message}
    if session_id:
        payload["session_id"] = session_id
    async with client.stream(
        "POST",
        "/api/v1/agent/chat/stream",
        json=payload,
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        async for chunk in response.aiter_text():
            buffer += chunk
            while "\n\n" in buffer:
                block, buffer = buffer.split("\n\n", 1)
                blocks.append(block)
    return _parse_sse_blocks(blocks)


def _single(events: list[tuple[str, dict]], name: str) -> dict:
    for event_name, data in events:
        if event_name == name:
            return data
    raise AssertionError(f"缺少 {name} 事件")


def _status_items(events: list[tuple[str, dict]]) -> list[dict]:
    return [data for name, data in events if name == "status"]


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_golden_sample_a_named_dish_with_budget() -> None:
    """样例A:点名辣子鸡+2人+预算60块 → 规划分支下线后走单菜做法分支(无 plan/步骤/追问)。"""
    client = await _client()
    async with client:
        events = await _stream(client, "明天中午想吃辣子鸡,我们俩,预算 60 块")

    names = [name for name, _ in events]
    assert names[-1] == "done"
    assert "text" in names
    assert "plan" not in names
    assert "thinking" not in names  # 未配置云端模型时无思维链事件
    # 本地库命中给做法:末尾带"要不要换通用做法"的反问 chips
    assert "questions" in names
    assert _single(events, "questions")["items"] == ["不用本地菜谱库的做法"]

    # 步骤直播:首事件必须是"准备会话"running,每条 status 都带动态步骤名
    assert events[0][0] == "status"
    assert events[0][1]["step"] == "session"
    assert events[0][1]["state"] == "running"
    status_items = _status_items(events)
    assert all(item.get("label") for item in status_items)
    assert any("本地菜谱库" in item["label"] for item in status_items)
    assert any(
        item["step"] == "parse_request" and item["state"] == "done"
        for item in status_items
    )
    # 请求解析只覆盖输入规范化，必须在后续路由/工具活动前结束，不能悬挂在顶部。
    parse_done_index = next(
        index for index, item in enumerate(status_items)
        if item["step"] == "parse_request" and item["state"] == "done"
    )
    route_running_index = next(
        index for index, item in enumerate(status_items)
        if item["step"] == "route_request" and item["state"] == "running"
    )
    assert parse_done_index < route_running_index

    done = _single(events, "done")
    assert done["session_id"]
    assert done["route"] == "kitchen_recipe"

    text = next(data for name, data in events if name == "text")["delta"]
    assert "辣子鸡" in text


def test_chat_diner_parser_accepts_ge_before_person() -> None:
    assert _extract_diners("2个人，你给我推荐吧") == 2
    assert _extract_diners("两个人") == 2
    assert _extract_diners("2个菜") is None
    assert _normalize_request_text("2个人") == "2人"


def test_chat_route_separates_general_and_kitchen_topics() -> None:
    assert _fallback_route("你好") == "general"
    assert _fallback_route("今天天气怎么样") == "general"
    assert _fallback_route("番茄怎么保存") == "kitchen_qa"
    assert _fallback_route("土豆能做什么菜") == "kitchen_qa"
    assert _fallback_route("我想吃焖面条") == "kitchen_recipe"
    assert _fallback_route("我想吃鲤鱼焙面") == "kitchen_recipe"
    assert _fallback_route("想吃辣子鸡，给我个方案") == "kitchen_plan"
    assert _fallback_route("想吃辣子鸡，两个人，预算60元") == "kitchen_plan"
    assert _fallback_route("帮我看看冰箱里有什么") == "kitchen_plan"
    assert _fallback_route("安排两个人的晚餐") == "kitchen_plan"
    assert "实时天气" in _fallback_general_text("今天天气怎么样")


async def test_general_chat_does_not_emit_planning_events() -> None:
    client = await _client()
    async with client:
        events = await _stream(client, "你好")

    names = [name for name, _ in events]
    assert names[-1] == "done"
    assert "text" in names
    assert "thinking" not in names
    assert "plan" not in names
    # 闲聊也直播步骤:准备会话 → 理解需求 → 组织回答
    assert events[0][0] == "status"
    assert events[0][1]["step"] == "session"
    assert any(item["step"] == "compose_reply" for item in _status_items(events))
    assert all(item.get("label") for item in _status_items(events))
    assert _single(events, "done")["route"] == "general"


async def test_kitchen_qa_does_not_emit_planning_events() -> None:
    client = await _client()
    async with client:
        events = await _stream(client, "番茄怎么保存")

    names = [name for name, _ in events]
    assert names[-1] == "done"
    assert "text" in names
    assert "thinking" not in names
    assert "plan" not in names
    # 问答也直播步骤:准备会话 → 理解需求 → 检查用餐限制 → 组织回答
    assert events[0][0] == "status"
    assert events[0][1]["step"] == "session"
    assert any(item["step"] == "compose_reply" for item in _status_items(events))
    assert any(
        item["step"] == "get_member_constraints" and item["state"] == "done"
        for item in _status_items(events)
    )
    # 顺序:检查用餐限制必须排在组织回答之前
    step_keys = [(item["step"], item["state"]) for item in _status_items(events)]
    assert step_keys.index(("get_member_constraints", "done")) < step_keys.index(
        ("compose_reply", "running")
    )
    assert _single(events, "done")["route"] == "kitchen_qa"


async def test_unknown_recipe_uses_recipe_route_without_plan() -> None:
    client = await _client()
    async with client:
        events = await _stream(client, "我想吃鲤鱼焙面")

    names = [name for name, _ in events]
    assert names[-1] == "done"
    assert "text" in names
    assert "plan" not in names
    # 本地菜谱库查询步骤必须出现(未命中也要如实说明)
    assert any(
        "本地菜谱库" in item.get("label", "") for item in _status_items(events)
    )
    done = _single(events, "done")
    assert done["route"] == "kitchen_recipe"
    assert done["search_query"] == "鲤鱼焙面"


async def test_golden_sample_c_vague_request_goes_qa() -> None:
    """样例C:规划分支下线后,模糊吃饭诉求按厨房问答对话处理,不再追问人数/预算。"""
    client = await _client()
    async with client:
        events = await _stream(client, "晚上吃点清淡的")

    names = [name for name, _ in events]
    assert "plan" not in names
    assert "questions" not in names
    assert "status" in names  # 问答对话同样直播步骤
    assert "text" in names
    assert _single(events, "done")["route"] == "kitchen_qa"


async def test_fuzzy_ingredient_search_returns_candidates() -> None:
    """模糊查询:今晚想吃虾 → candidates 候选列表带过敏标注,无 run/步骤事件。"""
    client = await _client()
    async with client:
        events = await _stream(client, "今晚想吃虾")

    names = [name for name, _ in events]
    assert "candidates" in names
    assert "plan" not in names
    assert "text" in names
    assert "thinking" in names  # 规则分支也给出真实执行步骤的思考过程
    # 步骤直播:查过冰箱、查过本地库且命中数写实
    assert any("冰箱" in item["label"] for item in _status_items(events))
    assert any(
        item["step"] == "search_recipe_candidates"
        and item["state"] == "done"
        and "命中" in item["detail"]
        for item in _status_items(events)
    )
    done = _single(events, "done")
    assert done["route"] == "kitchen_recipe"
    assert done["provider"] is None  # 列表是规则召回,不依赖模型

    candidates = _single(events, "candidates")
    assert candidates["query"] == "虾"
    item_names = {item["name"] for item in candidates["items"]}
    assert "虾仁炒蛋" in item_names
    assert "白灼虾" in item_names
    conflict = next(item for item in candidates["items"] if item["name"] == "白灼虾")
    assert conflict["conflict_level"] == "forbidden"  # 对象虾过敏 → 红线标注

    # 菜单列表统一带库存标注:虾仁炒蛋用冰箱的鸡蛋,虾仁不在库存如实标缺
    egg_item = next(item for item in candidates["items"] if item["name"] == "虾仁炒蛋")
    assert "鸡蛋" in egg_item["used_fridge"]
    assert "虾仁" in egg_item["missing"]

    text = next(data for name, data in events if name == "text")["delta"]
    assert "虾" in text


async def test_fuzzy_dish_name_takes_priority_over_ingredient() -> None:
    """菜名精确命中优先:我想吃虾仁炒蛋 直接给做法,不发候选列表。"""
    client = await _client()
    async with client:
        events = await _stream(client, "我想吃虾仁炒蛋")

    names = [name for name, _ in events]
    assert "candidates" not in names
    assert "text" in names
    assert _single(events, "done")["route"] == "kitchen_recipe"
    # 本地库命中要在步骤里点名
    assert any(
        item["state"] == "done" and "虾仁炒蛋" in item["detail"]
        for item in _status_items(events)
    )
    text = next(data for name, data in events if name == "text")["delta"]
    assert "虾仁炒蛋" in text


async def test_inventory_menu_ranks_expiring_first() -> None:
    """库存菜单:纯文本菜单与其他回复统一样式,临期优先排序,行内标注用冰箱/缺料。"""
    client = await _client()
    async with client:
        events = await _stream(client, "根据冰箱库存去生成一些菜单供我选，优先使用临期的")

    names = [name for name, _ in events]
    assert "candidates" not in names  # 与其他回复统一:不再发候选卡
    assert "plan" not in names
    assert "text" in names
    assert "thinking" in names  # 库存菜单的思考过程(读取冰箱/排序依据)
    # 步骤直播:调取冰箱 → 按现有食材匹配 → 核对忌口,全链路可见
    status_items = _status_items(events)
    assert any("冰箱" in item["label"] for item in status_items)
    assert any(
        item["step"] == "search_recipe_candidates"
        and item["state"] == "done"
        and item["detail"]
        for item in status_items
    )
    assert any(item["step"] == "filter_hard_constraints" for item in status_items)

    text = "".join(data.get("delta", "") for name, data in events if name == "text")
    assert "我看了下冰箱" in text
    assert "临期" in text
    assert "1. **" in text  # 编号菜名列表
    assert "还缺" in text  # 缺料标注
    assert "想看哪道菜的做法" in text
    assert _single(events, "done")["route"] == "kitchen_recipe"


async def test_list_sessions_returns_recent() -> None:
    """会话列表:对话后可列出,标题取首条消息前20字,按最近更新倒序,空会话不出现。"""
    client = await _client()
    async with client:
        events = await _stream(client, "明天中午我俩想吃豆角焖面,预算 60 块")
        session_id = _single(events, "done")["session_id"]
        await _stream(client, "那做法详细一点")
        # 再造一个空会话(只有一条 user 消息也算有消息;真正空会话无法从接口造,直接验列表内容)
        sessions_response = await client.get("/api/v1/agent/chat/sessions")

    assert sessions_response.status_code == 200
    sessions = sessions_response.json()
    assert isinstance(sessions, list) and sessions
    target = next(item for item in sessions if item["session_id"] == session_id)
    assert target["title"] == "明天中午我俩想吃豆角焖面,预算 60 块"[:20]
    assert target["updated_at"].endswith("+00:00")
    # 倒序:第一条应为最近更新的会话
    updated = [item["updated_at"] for item in sessions]
    assert updated == sorted(updated, reverse=True)


async def test_delete_session_removes_messages() -> None:
    """删除会话:消息一并删除,原会话 404,列表不含,重复删除 404。"""
    client = await _client()
    async with client:
        events = await _stream(client, "明天中午我俩想吃豆角焖面,预算 60 块")
        session_id = _single(events, "done")["session_id"]

        delete_response = await client.delete(f"/api/v1/agent/chat/{session_id}")
        assert delete_response.status_code == 200
        assert delete_response.json() == {"deleted": True}

        gone = await client.get(f"/api/v1/agent/chat/{session_id}")
        assert gone.status_code == 404

        listed = await client.get("/api/v1/agent/chat/sessions")
        assert all(item["session_id"] != session_id for item in listed.json())

        repeat = await client.delete(f"/api/v1/agent/chat/{session_id}")
        assert repeat.status_code == 404


async def test_clear_workspace_sessions_preserves_current_and_other_workspace() -> None:
    """批量清理只作用于目标工作区，并完整保留显式排除的当前会话。"""
    from app.db.models import AgentChatMessage, AgentChatSession, Workspace
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        session.add(Workspace(id="other-workspace", name="另一个家庭", kind="personal"))
        session.flush()
        other_session = AgentChatSession(workspace_id="other-workspace", title="其他家庭对话")
        session.add(other_session)
        session.flush()
        session.add(
            AgentChatMessage(
                session_id=other_session.id,
                role="user",
                content="另一个家庭的对话",
            )
        )
        other_id = other_session.id
        session.commit()

    client = await _client()
    async with client:
        current_events = await _stream(client, "当前对话：今晚吃清淡一点")
        current_id = _single(current_events, "done")["session_id"]
        old_events = await _stream(client, "历史对话：明天吃面")
        old_id = _single(old_events, "done")["session_id"]

        cleared = await client.delete(
            "/api/v1/agent/chat/sessions",
            params={
                "workspace_id": "home-workspace",
                "exclude_session_id": current_id,
            },
        )
        assert cleared.status_code == 200
        assert cleared.json()["deleted_sessions"] >= 1
        assert cleared.json()["deleted_messages"] >= 1

        count = await client.get(
            "/api/v1/agent/chat/sessions/count",
            params={
                "workspace_id": "home-workspace",
                "exclude_session_id": current_id,
            },
        )
        assert count.status_code == 200
        assert count.json() == {"count": 0}

        assert (await client.get(f"/api/v1/agent/chat/{current_id}")).status_code == 200
        assert (await client.get(f"/api/v1/agent/chat/{old_id}")).status_code == 404
        assert (await client.get(f"/api/v1/agent/chat/{other_id}")).status_code == 200

        repeated = await client.delete(
            "/api/v1/agent/chat/sessions",
            params={
                "workspace_id": "home-workspace",
                "exclude_session_id": current_id,
            },
        )
        assert repeated.status_code == 200
        assert repeated.json() == {"deleted_sessions": 0, "deleted_messages": 0}


async def test_session_restore_after_chat() -> None:
    """会话可恢复:对话后 GET 会话,消息(含用户与助手)持久化。"""
    client = await _client()
    async with client:
        events = await _stream(client, "明天中午我俩想吃豆角焖面,预算 60 块")
        session_id = _single(events, "done")["session_id"]

        response = await client.get(f"/api/v1/agent/chat/{session_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    roles = [message["role"] for message in body["messages"]]
    assert roles[0] == "user"
    assert "assistant" in roles


async def test_confirm_message_after_plan_removal() -> None:
    """规划分支下线后,确认类消息按对话处理,不再触发方案确认/采购链路。"""
    client = await _client()
    async with client:
        events = await _stream(client, "就做这套,确认")

    names = [name for name, _ in events]
    assert "plan" not in names
    assert "status" in names  # 对话处理同样直播步骤
    assert "text" in names
    assert _single(events, "done")["route"] == "kitchen_qa"


def _planning_context_with(constraints: list[MemberConstraint]) -> PlanningContext:
    return PlanningContext(
        workspace_id="w1",
        workspace_name="我家",
        site_name="家庭厨房",
        meal_service_id="ms1",
        meal_service_name="午餐",
        service_date=date(2026, 10, 7),
        expected_diners=2,
        budget_limit_cents=None,
        inventory=[
            InventoryLine(key="tofu", name="豆腐", quantity=1, unit="盒", expires_on=None)
        ],
        recipes=[],
        members=[],
        member_constraints=constraints,
        excluded_keys=[],
    )


def _demo_constraints() -> list[MemberConstraint]:
    return [
        MemberConstraint(
            user_id="u-lrio",
            member_name="Lrio",
            kind="restriction",
            level="warn",
            summary="Lrio·忌口tofu",
            ingredient_key="tofu",
        ),
        MemberConstraint(
            user_id="u-colin",
            member_name="Colin",
            kind="event",
            level="warn",
            summary="Colin·上火(忌辛辣、忌油炸)",
            avoid_tags=["辛辣", "油炸"],
        ),
    ]


def test_filter_constraints_by_selected_diners() -> None:
    """勾选了用餐成员时只保留他们的约束;未勾选/空列表视为全员。"""
    lrio, colin = _demo_constraints()
    assert _filter_constraints([lrio, colin], ["u-lrio"]) == [lrio]
    assert _filter_constraints([lrio, colin], None) == [lrio, colin]
    assert _filter_constraints([lrio, colin], []) == [lrio, colin]


def test_constraint_warnings_for_text() -> None:
    """回答文本涉及忌口食材 → 必须生成引用成员的提示;推荐类回答必带事件口味提醒。"""
    context = _planning_context_with(_demo_constraints())

    warnings = _constraint_warnings_for_text("玉米豆腐羹：嫩豆腐切小块，加玉米粒煮2分钟", context, True)
    assert any("Lrio·忌口tofu" in w and "豆腐" in w for w in warnings)
    assert any("Colin·上火" in w for w in warnings)

    # 与忌口食材无关、且非菜品推荐的回答 → 不产生提示
    assert _constraint_warnings_for_text("番茄怎么保存", context, False) == []

    block = _format_warning_block(warnings)
    assert block.startswith("\n\n**忌口与状态提示**")
    assert "Lrio" in block
    assert _format_warning_block([]) == ""


def test_wants_non_library_recipe_detection() -> None:
    """否定句识别:否定词+库来源词同时命中才判定,无关句不误伤。"""
    assert _wants_non_library_recipe("不想按照本地库的做法")
    assert _wants_non_library_recipe("不想接受本地库的做法")
    assert _wants_non_library_recipe("不用本地菜谱库的做法")
    assert not _wants_non_library_recipe("想吃辣子鸡")
    assert not _wants_non_library_recipe("换个做法")
    assert not _wants_non_library_recipe("不要香菜")


def test_looks_like_inventory_menu_phrases() -> None:
    """「看看冰箱里有什么」纯查询句式要命中;家电养护类问题不进库存菜单。"""
    assert _looks_like_inventory_menu("看看冰箱里有什么")
    assert _looks_like_inventory_menu("根据冰箱现有食材吧")
    assert _looks_like_inventory_menu("看看冰箱里有什么适合今天吃的")
    assert not _looks_like_inventory_menu("冰箱有什么异味怎么去除")
    assert not _looks_like_inventory_menu("冰箱温度调到多少合适")


async def test_fridge_inventory_query_returns_text_menu() -> None:
    """「看看冰箱里有什么」读真实冰箱出纯文本菜单,不落问答也不发候选卡。"""
    client = await _client()
    async with client:
        events = await _stream(client, "看看冰箱里有什么")

    names = [name for name, _ in events]
    assert "candidates" not in names
    text = "".join(data.get("delta", "") for name, data in events if name == "text")
    assert "我看了下冰箱" in text
    assert any("冰箱" in item["label"] for item in _status_items(events))
    assert _single(events, "done")["route"] == "kitchen_recipe"


async def test_non_library_refusal_resolves_dish_from_context() -> None:
    """「不想按照本地库的做法」对接上一轮:不查库,解析所指菜,按通用做法生成。"""
    client = await _client()
    async with client:
        events1 = await _stream(client, "我想吃虾仁炒蛋")
        session_id = _single(events1, "done")["session_id"]
        events = await _stream(client, "不想按照本地库的做法", session_id)

    names = [name for name, _ in events]
    assert "candidates" not in names
    steps = [item["step"] for item in _status_items(events)]
    assert "search_recipe_candidates" not in steps  # 不调取本地菜谱库
    assert "list_inventory" not in steps  # 通用做法用不到冰箱
    assert "get_member_constraints" in steps  # 忌口检查照常
    done = _single(events, "done")
    assert done["route"] == "kitchen_recipe"
    assert done["search_query"] == "虾仁炒蛋"  # 从上一轮解析出所指的菜
    text = "".join(data.get("delta", "") for name, data in events if name == "text")
    assert "虾仁炒蛋" in text


def test_dish_keyword_cleaning_for_external_search() -> None:
    """下厨房等外部搜索只留干净关键词:时间状语/意图动词/语气词一律剥掉。"""
    assert _dish_name_from_text("明天想吃黄焖鸡") == "黄焖鸡"
    assert _dish_name_from_text("今晚来一份可乐鸡翅吧") == "可乐鸡翅"
    assert _dish_name_from_text("鲅鱼饺子怎么做") == "鲅鱼饺子"

    dish_filter = _DishTagFilter()
    dish_filter.feed("推荐如下:\n<dishes>明天想吃黄焖鸡|番茄炒蛋</dishes>\n")
    assert dish_filter.dishes == ["黄焖鸡", "番茄炒蛋"]


async def test_fridge_ingredient_followup_routes_to_inventory() -> None:
    """「根据冰箱现有食材吧」这类省略句式必须进库存菜单分支,不能落到问答说没权限。"""
    client = await _client()
    async with client:
        events = await _stream(client, "根据冰箱现有食材吧")

    names = [name for name, _ in events]
    assert "candidates" not in names  # 统一纯文本样式,不再发候选卡
    text = "".join(data.get("delta", "") for name, data in events if name == "text")
    assert "我看了下冰箱" in text
    assert any("冰箱" in item["label"] for item in _status_items(events))
    assert _single(events, "done")["route"] == "kitchen_recipe"
