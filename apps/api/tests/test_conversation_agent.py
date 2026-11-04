import json
from datetime import date

import pytest
from sqlalchemy import select

from app.agent.conversation import run_conversation_agent, stream_conversation_agent
from app.db.models import MemberFoodPreference
from app.db.session import SessionLocal
from app.services.context_loader import (
    InventoryLine,
    MemberConstraint,
    PlanningContext,
    RecipeContext,
    RecipeIngredientLine,
)


def _context() -> PlanningContext:
    recipe = RecipeContext(
        id="r-1",
        name="番茄炒蛋",
        servings=2,
        duration_minutes=12,
        kcal_per_serving=180,
        estimated_cost_cents=800,
        tags=["快手"],
        taste_tags=["家常"],
        allergens=[],
        ingredients=[
            RecipeIngredientLine(key="tomato", name="番茄", quantity=2, unit="个", optional=False),
            RecipeIngredientLine(key="starch", name="淀粉", quantity=1, unit="勺", optional=False),
        ],
    )
    return PlanningContext(
        workspace_id="workspace",
        workspace_name="家",
        site_name="厨房",
        meal_service_id="lunch",
        meal_service_name="午餐",
        service_date=date(2026, 10, 8),
        expected_diners=2,
        budget_limit_cents=None,
        inventory=[InventoryLine(key="tomato", name="番茄", quantity=2, unit="个", expires_on=None)],
        recipes=[recipe],
        members=[],
        member_constraints=[],
        excluded_keys=[],
    )


class _ScriptedProvider:
    def supports_stream(self):
        return False

    def __init__(self, responses: list[dict], chat_text: str = ""):
        self.responses = responses
        self.calls: list[list[dict]] = []
        self.chat_text = chat_text

    async def chat_with_tools(self, *, messages, tools):
        self.calls.append(messages)
        return self.responses.pop(0)

    async def chat(self, *, messages):
        return self.chat_text


class _IntentProvider(_ScriptedProvider):
    def __init__(self, intent: dict, responses: list[dict]):
        super().__init__(responses)
        self.intent = intent

    async def chat(self, *, messages):
        return json.dumps(self.intent, ensure_ascii=False)


class _QueuedChatProvider(_ScriptedProvider):
    def __init__(self, chat_responses: list[dict], tool_responses: list[dict]):
        super().__init__(tool_responses)
        self.chat_responses = chat_responses

    async def chat(self, *, messages):
        return json.dumps(self.chat_responses.pop(0), ensure_ascii=False)


@pytest.mark.asyncio
async def test_agent_executes_readonly_tool_then_uses_result(monkeypatch) -> None:
    provider = _ScriptedProvider(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "get_inventory_summary", "arguments": "{}"},
                    }
                ],
            },
            {"role": "assistant", "content": "冰箱里有番茄，可以做番茄炒蛋。"},
        ]
    )
    monkeypatch.setattr("app.agent.conversation._load_context", lambda *args: _context())

    turn = await run_conversation_agent(
        provider=provider,
        workspace_id="workspace",
        site_id="kitchen",
        meal_service_id="lunch",
        diner_ids=None,
        user_text="冰箱有什么？",
        history=[],
        max_steps=4,
    )

    assert turn is not None
    assert "番茄" in turn.text
    assert [trace.tool_name for trace in turn.traces] == ["get_inventory_summary"]
    assert turn.traces[0].status == "success"
    assert any(message["role"] == "tool" for message in provider.calls[1])


@pytest.mark.asyncio
async def test_agent_caps_tool_loop(monkeypatch) -> None:
    inventory_response = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "get_inventory_summary", "arguments": "{}"},
            }
        ],
    }
    preference_response = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call-2",
                "type": "function",
                "function": {"name": "get_preference_summary", "arguments": "{}"},
            }
        ],
    }
    provider = _ScriptedProvider([inventory_response, preference_response])
    monkeypatch.setattr("app.agent.conversation._load_context", lambda *args: _context())

    turn = await run_conversation_agent(
        provider=provider,
        workspace_id="workspace",
        site_id="kitchen",
        meal_service_id="lunch",
        diner_ids=None,
        user_text="看看库存",
        history=[],
        max_steps=2,
    )

    assert turn is not None
    assert len(turn.traces) == 2
    assert len(provider.calls) == 2
    assert "避免重复查询" in turn.text


@pytest.mark.asyncio
async def test_stream_emits_running_before_tool_done(monkeypatch) -> None:
    provider = _ScriptedProvider([
        {"role": "assistant", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "get_inventory_summary", "arguments": "{}"}}]},
        {"role": "assistant", "content": "已查到库存。"},
    ])
    monkeypatch.setattr("app.agent.conversation._load_context", lambda *args: _context())
    events = [event async for event in stream_conversation_agent(provider=provider, workspace_id="workspace", site_id="kitchen", meal_service_id="lunch", diner_ids=None, user_text="看看库存", history=[], max_steps=4)]
    tool_events = [event for event in events if event["type"] == "tool"]
    assert [event["trace"].status for event in tool_events] == ["running", "success"]
    assert events.index(tool_events[0]) < events.index(tool_events[1])


@pytest.mark.asyncio
async def test_pantry_staple_is_not_procurement_suggestion(monkeypatch) -> None:
    provider = _ScriptedProvider([
        {"role": "assistant", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "check_recipe_availability", "arguments": '{"recipe":"番茄炒蛋"}'}}]},
        {"role": "assistant", "content": "可以做。"},
    ])
    context = _context(); context.pantry_keys = {"starch"}
    monkeypatch.setattr("app.agent.conversation._load_context", lambda *args: context)
    turn = await run_conversation_agent(provider=provider, workspace_id="workspace", site_id="kitchen", meal_service_id="lunch", diner_ids=None, user_text="番茄炒蛋能做吗", history=[], max_steps=4)
    assert turn is not None
    assert turn.procurement_suggestions == []
    assert turn.traces[0].status == "success"


@pytest.mark.asyncio
async def test_duplicate_tool_calls_reuse_result_without_spending_budget(monkeypatch) -> None:
    call = {"id": "call", "type": "function", "function": {"name": "search_recipes", "arguments": '{"query":"番茄"}'}}
    provider = _ScriptedProvider([
        {"role": "assistant", "tool_calls": [call, {**call, "id": "call-2"}, {**call, "id": "call-3"}]},
        {"role": "assistant", "content": "番茄炒蛋可以做。"},
    ])
    monkeypatch.setattr("app.agent.conversation._load_context", lambda *args: _context())
    turn = await run_conversation_agent(provider=provider, workspace_id="workspace", site_id="kitchen", meal_service_id="lunch", diner_ids=None, user_text="查番茄菜", history=[], max_steps=6)
    assert turn is not None
    assert len(turn.traces) == 3
    assert sum(trace.duration_ms > 0 for trace in turn.traces) == 1
    assert sum("已复用本轮结果" in trace.output_summary for trace in turn.traces) == 2


@pytest.mark.asyncio
async def test_procurement_suggestions_are_deduplicated_across_recipe_tools(monkeypatch) -> None:
    provider = _ScriptedProvider([
        {"role": "assistant", "tool_calls": [{"id": "detail", "type": "function", "function": {"name": "get_recipe_detail", "arguments": '{"recipe":"番茄炒蛋"}'}}]},
        {"role": "assistant", "tool_calls": [{"id": "availability", "type": "function", "function": {"name": "check_recipe_availability", "arguments": '{"recipe":"番茄炒蛋"}'}}]},
        {"role": "assistant", "content": "已整理好。"},
    ])
    monkeypatch.setattr("app.agent.conversation._load_context", lambda *args: _context())

    turn = await run_conversation_agent(
        provider=provider,
        workspace_id="workspace",
        site_id="kitchen",
        meal_service_id="lunch",
        diner_ids=None,
        user_text="番茄炒蛋缺什么",
        history=[],
        max_steps=6,
    )

    assert turn is not None
    assert [item["ingredient"] for item in turn.procurement_suggestions] == ["淀粉"]


def _menu_context() -> PlanningContext:
    def recipe(recipe_id: str, name: str, role: str, staple: str, sources: list[str], ingredients: list[RecipeIngredientLine]) -> RecipeContext:
        return RecipeContext(
            id=recipe_id, name=name, servings=2, duration_minutes=15, kcal_per_serving=100,
            estimated_cost_cents=100, tags=[], taste_tags=[], allergens=[], ingredients=ingredients,
            meal_role=role, staple_type=staple, protein_sources=sources, portion_confidence="complete",
        )

    base = _context()
    base.recipes = [
        recipe("p", "辣子鸡", "protein_dish", "无", ["鸡肉"], [RecipeIngredientLine(key="chicken", name="鸡肉", quantity=1, unit="份", optional=False)]),
        recipe("v", "清炒时蔬", "vegetable_dish", "无", [], [RecipeIngredientLine(key="greens", name="青菜", quantity=1, unit="份", optional=False)]),
        recipe("r", "大锅米饭", "staple_side", "米饭", [], [RecipeIngredientLine(key="rice", name="大米", quantity=1, unit="kg", optional=False)]),
    ]
    base.inventory = [
        InventoryLine(key="chicken", name="鸡肉", quantity=1, unit="份", expires_on=None),
        InventoryLine(key="greens", name="青菜", quantity=1, unit="份", expires_on=None),
        InventoryLine(key="rice", name="大米", quantity=1, unit="kg", expires_on=None),
    ]
    return base


@pytest.mark.asyncio
async def test_recommend_menu_returns_structured_menu(monkeypatch) -> None:
    provider = _ScriptedProvider([
        {"role": "assistant", "tool_calls": [{"id": "menu", "type": "function", "function": {"name": "recommend_menu", "arguments": '{"dish_count":2,"staple":"米饭"}'}}]},
        {"role": "assistant", "content": "已为你整理菜单。"},
    ])
    monkeypatch.setattr("app.agent.conversation._load_context", lambda *args: _menu_context())
    turn = await run_conversation_agent(provider=provider, workspace_id="workspace", site_id="kitchen", meal_service_id="lunch", diner_ids=None, user_text="两人两道菜配米饭", history=[], max_steps=6)
    assert turn is not None
    assert turn.menu_recommendation is not None
    assert turn.menu_recommendation["staple"]["type"] == "米饭"
    assert turn.menu_recommendation["staple"]["suggested_quantity"] == "生米约 160g"
    assert turn.menu_recommendation["meal_structure"] == "1 荤 + 1 素 + 米饭"
    assert {item["name"] for item in turn.menu_recommendation["items"]} == {"辣子鸡", "清炒时蔬"}


@pytest.mark.asyncio
async def test_structured_intent_locks_named_dish_diners_and_budget(monkeypatch) -> None:
    provider = _IntentProvider(
        {
            "task_type": "meal_plan",
            "diners": 2,
            "budget_limit_cents": 6000,
            "requested_dishes": ["辣子鸡"],
            "excluded_dishes": [],
            "preferred_ingredients": [],
            "excluded_ingredients": [],
            "style": "辣",
            "preference_updates": [],
        },
        [{"role": "assistant", "content": "已经按你的要求安排好了。"}],
    )
    monkeypatch.setattr("app.agent.conversation._load_context", lambda *args: _menu_context())

    turn = await run_conversation_agent(
        provider=provider,
        workspace_id="workspace",
        site_id="kitchen",
        meal_service_id="lunch",
        diner_ids=None,
        user_text="明天中午想吃辣子鸡，我们俩，预算 60 元",
        history=[],
        max_steps=6,
    )

    assert turn is not None
    assert turn.menu_recommendation is not None
    assert turn.menu_recommendation["diners"] == 2
    assert "辣子鸡" in {item["name"] for item in turn.menu_recommendation["items"]}
    snapshot = turn.menu_recommendation["constraint_snapshot"]
    assert snapshot["required_dishes"] == ["辣子鸡"]
    assert snapshot["budget_limit_cents"] == 6000
    assert [trace.tool_name for trace in turn.traces].count("recommend_menu") == 1


@pytest.mark.asyncio
async def test_explicit_dislike_is_persisted_and_applied_to_recomputed_menu() -> None:
    provider = _IntentProvider(
        {
            "task_type": "preference_update",
            "diners": 2,
            "budget_limit_cents": None,
            "requested_dishes": [],
            "excluded_dishes": ["拍黄瓜"],
            "preferred_ingredients": [],
            "excluded_ingredients": [],
            "style": "",
            "preference_updates": [
                {
                    "target_type": "dish",
                    "target_name": "拍黄瓜",
                    "preference": "dislike",
                    "explicit": True,
                }
            ],
        },
        [{"role": "assistant", "content": "已记住，并重新安排了菜单。"}],
    )

    turn = await run_conversation_agent(
        provider=provider,
        workspace_id="home-workspace",
        site_id="home-kitchen",
        meal_service_id="home-lunch",
        diner_ids=["home-me", "home-partner"],
        user_text="我不喜欢吃拍黄瓜",
        history=[],
        max_steps=6,
    )

    assert turn is not None
    assert turn.preference_updates[0]["status"] == "skipped"
    assert "upsert_member_preference" in [trace.tool_name for trace in turn.traces]
    assert turn.menu_recommendation is not None
    assert all(item["name"] != "拍黄瓜" for item in turn.menu_recommendation["items"])
    assert "拍黄瓜" in turn.menu_recommendation["constraint_snapshot"]["excluded_dishes"]
    with SessionLocal() as session:
        saved = session.scalar(
            select(MemberFoodPreference).where(
                MemberFoodPreference.workspace_id == "home-workspace",
                MemberFoodPreference.user_id == "home-me",
                MemberFoodPreference.target_name == "拍黄瓜",
            )
        )
        assert saved is None


@pytest.mark.asyncio
async def test_local_miss_generates_ephemeral_recipe_and_uses_it_in_menu(monkeypatch) -> None:
    provider = _QueuedChatProvider(
        [
            {
                "task_type": "meal_plan",
                "diners": 2,
                "budget_limit_cents": 5000,
                "requested_dishes": ["星河豆腐"],
                "excluded_dishes": [],
                "preferred_ingredients": [],
                "excluded_ingredients": [],
                "style": "家常",
                "preference_updates": [],
            },
            {
                "name": "星河豆腐",
                "servings": 2,
                "duration_minutes": 20,
                "ingredients": [
                    {"name": "豆腐", "quantity": 300, "unit": "g", "optional": False}
                ],
                "steps": ["豆腐切块", "下锅烧熟并调味"],
                "meal_role": "mixed_dish",
                "staple_type": "无",
                "protein_sources": ["豆腐"],
                "taste_tags": ["家常"],
                "allergens": [],
            },
        ],
        [{"role": "assistant", "content": "星河豆腐采用模型生成做法。"}],
    )
    monkeypatch.setattr("app.agent.conversation._load_context", lambda *args: _menu_context())

    events = [
        event
        async for event in stream_conversation_agent(
            provider=provider,
            workspace_id="workspace",
            site_id="kitchen",
            meal_service_id="lunch",
            diner_ids=None,
            user_text="两个人想吃星河豆腐，预算 50 元",
            history=[],
            max_steps=6,
        )
    ]

    generated = next(event for event in events if event["type"] == "recipe_generated")
    assert generated["data"]["source"] == "model_generated"
    turn = next(event["turn"] for event in events if event["type"] == "final")
    assert turn.menu_recommendation is not None
    item = next(item for item in turn.menu_recommendation["items"] if item["name"] == "星河豆腐")
    assert item["source"] == "model_generated"
    assert turn.menu_recommendation["budget_status"] == "partial"


@pytest.mark.asyncio
async def test_menu_gap_generates_ephemeral_candidate_then_recomposes(monkeypatch) -> None:
    provider = _QueuedChatProvider(
        [
            {
                "task_type": "meal_plan",
                "diners": 2,
                "budget_limit_cents": None,
                "requested_dishes": [],
                "excluded_dishes": [],
                "preferred_ingredients": [],
                "excluded_ingredients": [],
                "style": "家常",
                "preference_updates": [],
            },
            {
                "name": "蒜蓉生菜",
                "servings": 2,
                "duration_minutes": 10,
                "ingredients": [
                    {"name": "生菜", "quantity": 300, "unit": "g", "optional": False}
                ],
                "steps": ["生菜洗净", "蒜蓉炒香后快速翻炒生菜"],
                "meal_role": "vegetable_dish",
                "staple_type": "无",
                "protein_sources": [],
                "taste_tags": ["家常"],
                "allergens": [],
            },
        ],
        [{"role": "assistant", "content": "已补齐一份模型生成的素菜。"}],
    )
    context = _menu_context()
    context.recipes = [recipe for recipe in context.recipes if recipe.meal_role != "vegetable_dish"]
    monkeypatch.setattr("app.agent.conversation._load_context", lambda *args: context)

    events = [
        event
        async for event in stream_conversation_agent(
            provider=provider,
            workspace_id="workspace",
            site_id="kitchen",
            meal_service_id="lunch",
            diner_ids=None,
            user_text="两个人吃家常晚餐",
            history=[],
            max_steps=6,
        )
    ]

    assert any(event["type"] == "recipe_generated" for event in events)
    turn = next(event["turn"] for event in events if event["type"] == "final")
    assert turn.menu_recommendation is not None
    vegetable = next(
        item
        for item in turn.menu_recommendation["items"]
        if item["role_label"] == "素菜"
    )
    assert vegetable["name"] == "蒜蓉生菜"
    assert vegetable["source"] == "model_generated"


@pytest.mark.asyncio
async def test_unsafe_generated_menu_candidate_keeps_local_fallback(monkeypatch) -> None:
    provider = _QueuedChatProvider(
        [
            {
                "task_type": "meal_plan",
                "diners": 2,
                "budget_limit_cents": None,
                "requested_dishes": [],
                "excluded_dishes": [],
                "preferred_ingredients": [],
                "excluded_ingredients": [],
                "style": "家常",
                "preference_updates": [],
            },
            {
                "name": "虾仁炒青菜",
                "servings": 2,
                "duration_minutes": 10,
                "ingredients": [
                    {"name": "虾仁", "quantity": 200, "unit": "g", "optional": False}
                ],
                "steps": ["虾仁洗净", "炒熟后调味"],
                "meal_role": "vegetable_dish",
                "staple_type": "无",
                "protein_sources": [],
                "taste_tags": ["家常"],
                "allergens": ["虾仁"],
            },
        ],
        [{"role": "assistant", "content": "本地方案已保留。"}],
    )
    context = _menu_context()
    context.recipes = [recipe for recipe in context.recipes if recipe.meal_role != "vegetable_dish"]
    context.member_constraints = [
        MemberConstraint("member", "家人", "restriction", "forbidden", "过敏虾仁", "shrimp")
    ]
    context.ingredient_names = {"shrimp": "虾仁"}
    monkeypatch.setattr("app.agent.conversation._load_context", lambda *args: context)

    events = [
        event
        async for event in stream_conversation_agent(
            provider=provider,
            workspace_id="workspace",
            site_id="kitchen",
            meal_service_id="lunch",
            diner_ids=None,
            user_text="两个人吃家常晚餐",
            history=[],
            max_steps=6,
        )
    ]

    assert not any(event["type"] == "recipe_generated" for event in events)
    turn = next(event["turn"] for event in events if event["type"] == "final")
    assert turn.menu_recommendation is not None
    assert all(item["name"] != "虾仁炒青菜" for item in turn.menu_recommendation["items"])
    assert any("还缺少一项蔬菜" in note for note in turn.menu_recommendation["relaxations"])


@pytest.mark.asyncio
async def test_dsml_content_is_not_shown_and_alias_is_executed(monkeypatch) -> None:
    leaked = """<|DSML|tool_calls>
<|DSML|invoke name="get_recipe_by_id">
<|DSML|parameter name="recipe" string="true">番茄炒蛋</|DSML|parameter>
</|DSML|invoke>
</|DSML|tool_calls>"""
    provider = _ScriptedProvider([
        {"role": "assistant", "content": leaked},
        {"role": "assistant", "content": "番茄炒蛋可以做。"},
    ])
    monkeypatch.setattr("app.agent.conversation._load_context", lambda *args: _context())
    turn = await run_conversation_agent(provider=provider, workspace_id="workspace", site_id="kitchen", meal_service_id="lunch", diner_ids=None, user_text="看看番茄炒蛋", history=[], max_steps=6)
    assert turn is not None
    assert "DSML" not in turn.text
    assert "可以做" in turn.text
    assert [trace.tool_name for trace in turn.traces] == ["get_recipe_detail"]


@pytest.mark.asyncio
async def test_unknown_tool_protocol_is_not_executed_or_shown(monkeypatch) -> None:
    leaked = '<|DSML|invoke name="delete_inventory"><|DSML|parameter name="id" string="true">1</|DSML|parameter></|DSML|invoke>'
    provider = _ScriptedProvider([{"role": "assistant", "content": leaked}])
    monkeypatch.setattr("app.agent.conversation._load_context", lambda *args: _context())
    turn = await run_conversation_agent(provider=provider, workspace_id="workspace", site_id="kitchen", meal_service_id="lunch", diner_ids=None, user_text="清空冰箱", history=[], max_steps=4)
    assert turn is not None
    assert turn.traces == []
    assert "DSML" not in turn.text
    assert "delete_inventory" not in turn.text


@pytest.mark.asyncio
async def test_exhausted_budget_discards_protocol_and_uses_menu(monkeypatch) -> None:
    def call(name: str, arguments: str = "{}") -> dict:
        return {"role": "assistant", "tool_calls": [{"id": name, "type": "function", "function": {"name": name, "arguments": arguments}}]}

    provider = _ScriptedProvider(
        [
            call("get_member_constraints"),
            call("get_inventory_summary"),
            call("get_preference_summary"),
            call("recommend_menu"),
            call("search_recipes", '{"query":"青菜"}'),
            call("search_recipes", '{"query":"豆腐"}'),
        ],
        chat_text="<|DSML|tool_calls><|DSML|invoke name=\"get_recipe_by_id\"></|DSML|invoke>",
    )
    monkeypatch.setattr("app.agent.conversation._load_context", lambda *args: _menu_context())
    turn = await run_conversation_agent(provider=provider, workspace_id="workspace", site_id="kitchen", meal_service_id="lunch", diner_ids=None, user_text="今晚吃什么", history=[], max_steps=6)
    assert turn is not None
    assert "DSML" not in turn.text
    assert "辣子鸡" in turn.text
    assert turn.menu_recommendation is not None
    assert provider.chat_text not in turn.text
