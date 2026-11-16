"""Runtime tests exercise the actual graph, tools and worker business boundary."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select, text

from app import worker
from app.agent import durable_graph as graph
from app.db.models import Ingredient, Recipe, WorkspaceMember
from app.db.session import SessionLocal
from app.db.v2_models import (
    BackgroundJob,
    HouseholdProfile,
    InventoryLot,
    KnowledgeDocument,
    KnowledgeSource,
    ShoppingItem,
    ToolExecution,
    TurnRun,
)
from app.services import v2_runtime as runtime


def start(sessions=SessionLocal, *, chat_id=None, message="冰箱还有什么", actor=None):
    if actor is None:
        with sessions() as session:
            member = session.scalar(select(WorkspaceMember))
            actor = member.user_id, member.workspace_id
    response = runtime.create_turn(*actor, chat_id, message)
    job_id = worker.claim("graph-worker")
    snapshot = worker.snapshot_job(job_id, "graph-worker")
    return actor, response, snapshot


def seed_recipes(sessions, household):
    with sessions.begin() as session:
        session.add(
            HouseholdProfile(
                workspace_id=household, display_name="child", restrictions=["花生"], preferences=[]
            )
        )
        source = KnowledgeSource(name="approved test", approval="approved", license_note="test")
        session.add(source)
        session.flush()
        for index in range(5):
            recipe = Recipe(name=f"test-{index}", servings=3, duration_minutes=15)
            session.add(recipe)
            session.flush()
            session.add(
                KnowledgeDocument(
                    recipe_id=recipe.id,
                    source_id=source.id,
                    published=True,
                    content_version=1,
                    capabilities={"steps": True},
                    content={
                        "name": recipe.name,
                        "ingredients": ["花生" if index == 0 else "番茄"],
                        "steps": ["煮熟"],
                    },
                    search_text=recipe.name,
                )
            )


def seed_direction_recipes(sessions, household):
    with sessions.begin() as session:
        source = KnowledgeSource(name="direction test", approval="approved", license_note="test")
        session.add(source)
        session.flush()
        for name, category, ingredients, steps in (
            ("清蒸鲈鱼", "家常菜", ["番茄"], ["蒸熟"]),
            ("红烧茄子", "家常菜", ["番茄"], ["烧熟"]),
            ("番茄炒蛋", "家常菜", ["番茄", "鸡蛋"], ["炒熟"]),
            ("金枪鱼酱三明治", "早餐", ["番茄"], ["夹好"]),
            ("红烧酱汁", "酱料", ["番茄"], ["调匀"]),
        ):
            recipe = Recipe(name=name, servings=2, duration_minutes=20, steps=steps)
            session.add(recipe)
            session.flush()
            session.add(
                KnowledgeDocument(
                    recipe_id=recipe.id,
                    source_id=source.id,
                    published=True,
                    content_version=1,
                    capabilities={"steps": True},
                    content={
                        "name": name,
                        "category": category,
                        "ingredients": ingredients,
                        "steps": steps,
                    },
                    search_text=name,
                )
            )


def test_graph_real_tools_and_confirmation_boundary():
    actor, response, snapshot = start(message="买点鸡蛋")
    worker.execute(snapshot.job_id, "graph-worker")
    run = runtime.accessible_run(actor[0], response["run_id"])
    assert run.status == "waiting_confirmation"
    assert "尚未保存" in run.result["text"]
    assert run.state["budgets"] == {"tool_calls": 2, "model_calls": 3}
    assert "execute_tool" in run.state["trace"]
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ToolExecution)) == 2
        assert session.scalar(select(func.count()).select_from(ShoppingItem)) == 0
    action_id = run.result["pending_action"]["id"]
    result = runtime.decide_action(actor[0], action_id, True, "confirm-graph")
    assert result["status"] == "confirmed"
    assert runtime.decide_action(actor[0], action_id, True, "confirm-graph") == result
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ShoppingItem)) == 1


def test_multi_turn_menu_preserves_constraints_and_other_dishes():
    actor, response, snapshot = start(message="三人菜单，不吃鸡蛋")
    seed_recipes(SessionLocal, actor[1])
    worker.execute(snapshot.job_id, "graph-worker")
    first = runtime.accessible_run(actor[0], response["run_id"])
    assert first.status == "succeeded"
    assert len(first.result["menu_draft"]) == 3
    _, response, snapshot = start(chat_id=first.session_id, message="第二道换一道", actor=actor)
    worker.execute(snapshot.job_id, "graph-worker")
    second = runtime.accessible_run(actor[0], response["run_id"])
    assert second.state["constraints"]["diners"] == 3
    assert second.state["constraints"]["excluded_ingredients"] == ["花生", "鸡蛋"]
    old, new = first.result["menu_draft"], second.result["menu_draft"]
    assert old[0] == new[0] and old[2] == new[2] and old[1] != new[1]
    _, response, snapshot = start(
        chat_id=second.session_id, message="取消本次忌口，两人菜单", actor=actor
    )
    worker.execute(snapshot.job_id, "graph-worker")
    third = runtime.accessible_run(actor[0], response["run_id"])
    assert third.state["constraints"] == {"diners": 2, "excluded_ingredients": ["花生"]}


def test_bare_diner_reply_after_prompt_is_remembered():
    actor, response, snapshot = start(message="菜单")
    worker.execute(snapshot.job_id, "graph-worker")
    first = runtime.accessible_run(actor[0], response["run_id"])
    assert first.status == "waiting_input"
    assert "请明确本次用餐人数" in first.result["text"]

    _, response, snapshot = start(chat_id=first.session_id, message="2", actor=actor)
    worker.execute(snapshot.job_id, "graph-worker")
    second = runtime.accessible_run(actor[0], response["run_id"])
    assert second.state["constraints"]["diners"] == 2
    assert "请明确本次用餐人数" not in second.result["text"]


def test_unknown_citation_is_stripped_and_late_writes_rejected():
    class Fabricating:
        async def chat_with_tools(self, **kwargs):
            return {"content": "引用 [recipe:invented:v1]", "citations": ["recipe:invented:v1"]}

    _, _, snapshot = start()
    result = graph.compute(SessionLocal, snapshot, Fabricating())
    # Unread IDs are never accepted as evidence, but they must not dead-end the reply.
    assert result["status"] == "succeeded"
    assert result["text"] == "引用"
    assert result["citations"] == []
    assert result["evidence"] == []
    assert any("无法核验" in warning for warning in result["warnings"])
    with SessionLocal.begin() as session:
        session.get(BackgroundJob, snapshot.job_id).attempts += 1
    with pytest.raises(graph.ExecutionStopped, match="LEASE_LOST"):
        graph.compute(SessionLocal, snapshot)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2个人，你给我推荐吧", 2),
        ("两个人", 2),
        ("2人", 2),
        ("2位", 2),
        ("2口人", 2),
        ("三个人", 3),
        ("2个菜", None),
    ],
)
def test_explicit_constraints_parses_common_diner_phrases(text, expected):
    assert graph.explicit_constraints(text).get("diners") == expected


def test_explicit_constraints_parses_bare_diner_reply_only_when_expected():
    assert graph.explicit_constraints("2", expects_diners=True).get("diners") == 2
    assert graph.explicit_constraints("两", expects_diners=True).get("diners") == 2
    assert graph.explicit_constraints("2个菜", expects_diners=True).get("diners") is None
    assert graph.explicit_constraints("2").get("diners") is None


def test_explicit_constraints_parses_light_style_without_confusing_negation():
    parsed = graph.explicit_constraints("两个人吃清淡一点")
    assert parsed["diners"] == 2
    assert parsed["dietary_style"] == ["清淡"]
    assert graph.explicit_constraints("不要清淡")["dietary_style"] == []


def test_explicit_constraints_strips_notes_from_excluded_ingredients():
    assert graph.explicit_constraints("不吃花生（过敏）")["excluded_ingredients"] == ["花生"]
    assert graph.explicit_constraints("不吃香菜和葱")["excluded_ingredients"] == ["香菜", "葱"]
    assert graph.explicit_constraints("不要大白菜、冬季黄心白菜最佳）")[
        "excluded_ingredients"
    ] == ["大白菜"]
    assert graph.explicit_constraints("不吃辣")["excluded_ingredients"] == ["辣"]


def test_citation_cleanup_keeps_recipe_lines_and_drops_empty_source_label():
    text = "清蒸鱼 [recipe:abc:v1]\n1. 洗净\n来源：[recipe:abc:v1]\n2. 蒸熟"
    assert graph.strip_citation_markers(text) == "清蒸鱼\n1. 洗净\n2. 蒸熟"


def test_requested_dish_names_require_explicit_dishes():
    assert graph.requested_dish_names("我今天中午想吃河南卤面，鸡蛋汤") == [
        "河南卤面", "鸡蛋汤",
    ]
    assert graph.requested_dish_names("我想吃河南卤面和鸡蛋汤") == ["河南卤面", "鸡蛋汤"]
    assert graph.requested_dish_names("午餐想吃番茄炒蛋") == ["番茄炒蛋"]
    assert graph.requested_dish_names("今天中午吃什么") == []
    assert graph.requested_dish_names("三人菜单，不吃鸡蛋") == []
    assert graph.requested_dish_names("第二道换一道") == []
    assert graph.requested_dish_names("我想吃虾，不要辣") == []
    assert graph.requested_dish_names("买点鸡蛋") == []
    assert graph.requested_dish_names("我想吃点什么") == []
    assert graph.requested_dish_names("我想吃你做的菜") == []
    assert graph.requested_dish_names("我想吃家里的饭") == []
    assert graph.requested_dish_names("我想吃河南卤面，两碗") == ["河南卤面"]
    assert graph.requested_dish_names("我想吃河南卤面，谢谢") == ["河南卤面"]


def test_recipe_views_drop_ingredient_metadata():
    content = {
        "name": "反沙芋头",
        "servings": 2,
        "ingredients": [
            {"name": "荔浦芋头、实惠新鲜）", "amount": "500克"},
            {"name": "盆", "amount": ""},
        ],
        "steps": ["芋头切条。", "炸制。"],
        "capabilities": {"quantities": False},
    }

    assert graph._recipe_ingredient_text(content, 2) == "荔浦芋头 500克"
    names, keys = graph._recipe_ingredient_values(content)
    assert names == ["荔浦芋头"]
    assert keys == []
    analysis = graph._inventory_analysis(content, [], set(), {}, 2)
    assert analysis["missing"] == ["荔浦芋头"]


def test_recipe_views_drop_parenthetical_note_fragments():
    content = {
        "name": "白菜拌芝麻",
        "servings": 3,
        "ingredients": [
            {"name": "大白菜", "amount": ""},
            {"name": "冬季黄心白菜最佳）", "amount": ""},
            {"name": "熟白芝麻", "amount": ""},
            {"name": "用于点缀）", "amount": ""},
        ],
        "steps": ["洗净。", "装盘。"],
        "capabilities": {"quantities": False},
    }

    assert graph._recipe_ingredient_text(content, 3) == "大白菜、熟白芝麻"
    names, keys = graph._recipe_ingredient_values(content)
    assert names == ["大白菜", "熟白芝麻"]
    assert keys == []
    analysis = graph._inventory_analysis(content, [], set(), {}, 3)
    assert analysis["missing"] == ["大白菜", "熟白芝麻"]


def test_requested_dish_names_strip_servings_and_time_metadata():
    standard = "我想做凉皮。请结合我家现有库存，告诉我是否够用，还缺什么，并给我具体做法。"
    assert graph.requested_dish_names(standard) == ["凉皮"]
    assert graph.requested_dish_names(
        "我想做凉皮（2人份，约30分钟）。请结合我家现有库存，告诉我是否够用、"
        "还缺什么，并给我具体做法。"
    ) == ["凉皮"]
    assert graph.requested_dish_names("我想做凉皮［2人份，约30分钟］") == ["凉皮"]
    assert graph.requested_dish_names("我想做凉皮 2人份") == ["凉皮"]
    assert graph.requested_dish_names("我想做凉皮，2人份") == ["凉皮"]
    assert graph.requested_dish_names("我想做凉皮，约三十分钟") == ["凉皮"]


@pytest.mark.parametrize("kind", ["allergy", "dislike"])
@pytest.mark.parametrize("message", ["我想吃虾了", "我要吃虾", "我想吃虾，不要辣"])
def test_shrimp_request_checks_registered_type_and_returns_verified_alternatives(kind, message):
    from app.db.models import Ingredient, RecipeIngredient

    actor, _, snapshot = start(message=message)
    seed_recipes(SessionLocal, actor[1])
    with SessionLocal.begin() as session:
        session.add(HouseholdProfile(
            workspace_id=actor[1], display_name="我", preferences=[],
            restrictions=[{"type": kind, "ingredient_key": "shrimp", "ingredient_name": "虾仁"}],
        ))
        tomato = session.scalar(select(Ingredient).where(Ingredient.key == "tomato"))
        for recipe in session.scalars(select(Recipe).where(Recipe.name.like("test-%"))):
            recipe.steps = ["煮熟"]
            session.add(RecipeIngredient(
                recipe_id=recipe.id, ingredient_id=tomato.id, quantity=None, unit=None,
            ))

    class MustNotCallModel:
        async def chat_with_tools(self, **kwargs):
            raise AssertionError("Registered restricted-food request must be server-rendered")

    result = graph.compute(SessionLocal, snapshot)
    assert result["status"] == "succeeded"
    expected = "虾仁过敏" if kind == "allergy" else "忌口虾仁"
    assert expected in result["text"]
    if kind == "allergy":
        assert "过敏" in result["text"]
    else:
        assert "过敏" not in result["text"]
    assert "核对已登记的过敏原与忌口" in result["thinking_steps"]
    assert "[recipe:" not in result["text"]
    assert 1 <= len(result["options"]) <= 3
    assert all(option["prompt"] for option in result["options"])
    assert "pending_action" not in result
    assert all("方向" in option["prompt"] for option in result["options"])
    assert all("虾仁" in option["prompt"] for option in result["options"])
    assert all("库存暂无记录" in option["reason"] for option in result["options"])
    assert all(option["missing"] == [] for option in result["options"])
    assert "库存暂无可用记录" in result["text"]
    assert "煮熟" not in result["text"]
    assert result["citations"]
    cited = [item for item in result["evidence"] if item["id"] in result["citations"]]
    assert len(cited) == len(result["citations"])
    assert all("花生" not in str(item["content"]["ingredients"]) for item in cited)
    assert "INVALID_CITATION" not in str(result)


def test_named_dishes_use_rag_then_active_stock_and_draft_procurement(monkeypatch):
    actor, _, snapshot = start(message="我今天中午想吃河南卤面，鸡蛋汤")

    def evidence(recipe_id: str, content: dict) -> dict:
        content = dict(content)
        capabilities = content.pop("capabilities", None)
        item = {
            "id": f"recipe:{recipe_id}:v1",
            "kind": "recipe",
            "recipe_id": recipe_id,
            "title": content["name"],
            "content_version": 1,
            "content": content,
        }
        if capabilities is not None:
            item["capabilities"] = capabilities
        return item

    noodle = evidence("named-noodle", {
        "name": "蒸卤面",
        "category": "家常菜",
        "servings": 2,
        "ingredients": [
            {"name": "五花肉", "ingredient_key": "pork_belly"},
            {"name": "芹菜", "ingredient_key": "celery"},
            {"name": "鲜面条", "ingredient_key": "noodles"},
            {"name": "青椒", "ingredient_key": "pepper_green"},
            {"name": "盐", "ingredient_key": "salt"},
            {"name": "如果附近菜市场没有", "ingredient_key": "misc_bad_1"},
            {"name": "请参考制作[焖面]）", "ingredient_key": "misc_bad_2"},
            {"name": "需带笼屉", "ingredient_key": "misc_bad_3"},
        ],
        "capabilities": {"quantities": False},
        "steps": ["蒸熟"],
        "allergens": ["麸质"],
    })
    soup = evidence("named-soup", {
        "name": "西红柿鸡蛋汤",
        "category": "汤",
        "servings": 2,
        "ingredients": [
            {"name": "番茄", "ingredient_key": "tomato", "quantity": "0.2", "unit": "kg"},
            {"name": "鸡蛋", "ingredient_key": "egg", "quantity": "2", "unit": "个"},
            {"name": "盐", "ingredient_key": "salt"},
        ],
        "capabilities": {"quantities": True},
        "steps": ["煮开"],
        "allergens": ["蛋"],
    })
    queries = []

    def fake_search(query, **_kwargs):
        queries.append(query)
        item = soup if "鸡蛋汤" in query else noodle
        return graph.ToolResult(
            status="ok", data={"evidence": [item]}, evidence_ids=[item["id"]],
        )

    monkeypatch.setattr(graph, "search_retrieval", fake_search)

    with SessionLocal.begin() as session:
        session.execute(delete(InventoryLot).where(InventoryLot.workspace_id == actor[1]))
        session.execute(delete(HouseholdProfile).where(HouseholdProfile.workspace_id == actor[1]))

        def ingredient(key, name, category):
            row = session.scalar(select(Ingredient).where(Ingredient.key == key))
            if row is None:
                row = Ingredient(key=key, name=name, category=category, default_unit="g")
                session.add(row)
                session.flush()
            return row

        pork = ingredient("pork_belly", "五花肉", "肉禽")
        ingredient("celery", "芹菜", "蔬菜")
        ingredient("noodles", "挂面", "主食")
        ingredient("pepper_green", "青椒", "蔬菜")
        tomato = ingredient("tomato", "番茄", "蔬菜")
        egg = ingredient("egg", "鸡蛋", "蛋奶")

        def add_lot(row, status, quantity, unit):
            session.add(InventoryLot(
                workspace_id=actor[1],
                ingredient_id=row.id,
                ingredient_key=row.key,
                name=row.name,
                quantity_status="known",
                quantity=quantity,
                unit=unit,
                confirmed_at=datetime.now(UTC),
                source_type="test",
                source_id=f"named-{row.key}-{status}",
                status=status,
            ))

        add_lot(pork, "consumed", Decimal("1"), "kg")
        add_lot(tomato, "active", Decimal("0.5"), "kg")
        add_lot(egg, "active", Decimal("6"), "个")

    class MustNotCallModel:
        async def chat_with_tools(self, **kwargs):
            raise AssertionError("Named-dish flow must be server-rendered")

    result = graph.compute(SessionLocal, snapshot)
    assert queries == ["河南卤面", "鸡蛋汤"]
    assert result["status"] == "waiting_confirmation"
    assert result["text"].count("做法来源：本地菜谱") == 2
    assert "蒸卤面" in result["text"]
    assert "西红柿鸡蛋汤" in result["text"]
    assert "库存：可以直接做。" in result["text"]
    assert "缺少：五花肉、芹菜、鲜面条、青椒" in result["text"]
    assert "整理成采购清单草稿" in result["text"]
    assert "要我把缺少的" not in result["text"]
    action = result["action"]
    assert action["type"] == "add_procurement"
    items = action["payload"]["items"]
    assert [item["name"] for item in items] == ["五花肉", "芹菜", "鲜面条", "青椒"]
    assert all(item["quantity_status"] == "unknown" for item in items)
    assert all(item["quantity"] is None and item["unit"] is None for item in items)
    runtime.validate_action("add_procurement", action["payload"])
    assert "食材：" in result["text"]
    assert "步骤：" in result["text"]
    assert "1. 蒸熟" in result["text"]
    assert "1. 煮开" in result["text"]
    assert "如果附近菜市场没有" not in result["text"]
    assert "请参考制作" not in result["text"]
    assert "需带笼屉" not in result["text"]
    assert "- 番茄：0.2kg（库存 0.5kg）" in result["text"]
    assert "- 鸡蛋：2个（库存 6个）" in result["text"]
    assert "MISSING_RECIPE_CITATION" not in str(result)
    assert result["citations"] == [noodle["id"], soup["id"]]
    assert result["_state"]["budgets"] == {"tool_calls": 2, "model_calls": 2}
    assert result["thinking_steps"] == []
    assert result["warnings"] == []


def test_named_dish_falls_back_to_recipe_tutorial_then_checks_active_stock(monkeypatch):
    actor, _, snapshot = start(message="我今天中午想吃卤面")
    queries = []

    def fake_search(query, **_kwargs):
        queries.append(query)
        return graph.ToolResult(status="ok", data={"evidence": []}, evidence_ids=[])

    async def fake_fallback(dish, *, provider=None):
        assert dish == "卤面"
        assert provider is not None
        return {
            "name": "家常卤面",
            "servings": 2,
            "ingredients": [
                {"name": "五花肉", "ingredient_key": "", "amount": "150克"},
                {"name": "芹菜", "ingredient_key": "", "amount": "1把"},
                {"name": "鲜面条", "ingredient_key": "", "amount": "300克"},
                {"name": "青椒", "ingredient_key": "", "amount": "1个"},
                {"name": "盐", "ingredient_key": "", "amount": "适量"},
            ],
            "steps": [
                "五花肉切片，芹菜切段，青椒切丝。",
                "面条拌少量油，铺在蒸屉上。",
                "铺上肉和菜，蒸熟后拌匀。",
            ],
            "allergens": [],
            "capabilities": {"quantities": False},
            "source_name": "网络教程",
            "source_url": "https://www.xiachufang.com/recipe/123/",
            "unverified": True,
            "fallback_kind": "web",
        }

    monkeypatch.setattr(graph, "search_retrieval", fake_search)
    monkeypatch.setattr(graph, "lookup_recipe_tutorial", fake_fallback)
    with SessionLocal.begin() as session:
        session.execute(delete(InventoryLot).where(InventoryLot.workspace_id == actor[1]))
        session.execute(delete(HouseholdProfile).where(HouseholdProfile.workspace_id == actor[1]))

    class MustNotCallModel:
        async def chat_with_tools(self, **_kwargs):
            raise AssertionError("Named-dish flow must be server-rendered")

    result = graph.compute(SessionLocal, snapshot)
    assert queries == ["卤面"]
    assert result["status"] == "waiting_confirmation"
    assert "家常卤面" in result["text"]
    assert "做法来源：网络教程（https://www.xiachufang.com/recipe/123/）· 未核实" in result["text"]
    assert "食材：五花肉 150克、芹菜 1把、鲜面条 300克、青椒 1个、盐 适量" in result["text"]
    assert "步骤：" in result["text"]
    assert "1. 五花肉切片，芹菜切段，青椒切丝。" in result["text"]
    assert "3. 铺上肉和菜，蒸熟后拌匀。" in result["text"]
    assert "缺少：五花肉、芹菜、鲜面条、青椒" in result["text"]
    assert "采购时按实际需要调整" in result["text"]
    assert "菜谱未标注可靠克数" not in result["text"]
    assert "整理成采购清单草稿" in result["text"]
    assert "要我把缺少的" not in result["text"]
    action = result["action"]
    items = action["payload"]["items"]
    assert [item["name"] for item in items] == ["五花肉", "芹菜", "鲜面条", "青椒"]
    assert [item["required"] for item in items] == ["150克", "1把", "300克", "1个"]
    assert all(item["quantity_status"] == "unknown" for item in items)
    assert "已先检索" not in result["text"]
    assert "未检索到" not in result["text"]
    assert result["citations"] == []
    assert result["evidence"] == []
    assert result["thinking_steps"] == []
    assert result["warnings"] == []
    assert result["_state"]["budgets"] == {"tool_calls": 2, "model_calls": 2}


def test_named_dish_fallback_budget_caps_tutorial_lookups(monkeypatch):
    _, _, snapshot = start(message="我今天中午想吃卤面、焖面、炒面")
    calls = []

    def fake_search(query, **_kwargs):
        return graph.ToolResult(status="ok", data={"evidence": []}, evidence_ids=[])

    async def fake_fallback(dish, *, provider=None):
        calls.append(dish)
        return {
            "name": f"家常{dish}",
            "servings": 2,
            "ingredients": [
                {"name": "面条", "ingredient_key": "", "amount": "300克"},
            ],
            "steps": ["准备面条。", "煮熟后调味。"],
            "allergens": [],
            "capabilities": {"quantities": False},
            "source_name": "模型补充",
            "source_url": "",
            "unverified": True,
            "fallback_kind": "llm",
        }

    monkeypatch.setattr(graph, "search_retrieval", fake_search)
    monkeypatch.setattr(graph, "lookup_recipe_tutorial", fake_fallback)

    class MustNotCallModel:
        async def chat_with_tools(self, **_kwargs):
            raise AssertionError("Named-dish flow must be server-rendered")

    result = graph.compute(SessionLocal, snapshot)

    assert calls == ["卤面", "焖面"]
    assert "家常卤面" in result["text"]
    assert "家常焖面" in result["text"]
    assert "另外，炒面暂时没有可提供的做法" in result["text"]


def test_named_dish_fallback_without_model_explains_degradation(monkeypatch):
    _, _, snapshot = start(message="我今天中午想吃卤面")

    monkeypatch.setattr(
        graph,
        "search_retrieval",
        lambda query, **_kwargs: graph.ToolResult(
            status="ok", data={"evidence": []}, evidence_ids=[],
        ),
    )

    class ToolOnlyModel:
        async def chat_with_tools(self, **_kwargs):
            raise AssertionError("Named-dish flow must be server-rendered")

    # No key is configured in tests, so the real fallback returns None without
    # any network request and the answer must say why it degraded.
    result = graph.compute(SessionLocal, snapshot)

    assert result["status"] == "waiting_input"
    assert "「卤面」在本地菜谱库没有命中" in result["text"]
    assert "未配置补充模型" in result["text"]
    assert "action" not in result
    assert result["_state"]["budgets"] == {"tool_calls": 2, "model_calls": 2}


def _local_recipe(name: str, *, score: float) -> dict:
    return {
        "id": f"recipe:{name}:v1",
        "kind": "recipe",
        "title": name,
        "score": score,
        "content": {
            "name": name,
            "category": "家常菜",
            "servings": 2,
            "ingredients": [{"name": "面条", "ingredient_key": "noodles"}],
            "steps": ["煮熟面条。", "拌匀后出锅。"],
            "allergens": [],
        },
    }


def test_named_dish_keeps_local_recipe_when_title_does_not_overlap(monkeypatch):
    _, _, snapshot = start(message="我今天中午想吃卤面")
    calls = []

    async def fail_fallback(dish, *, provider=None):
        calls.append(dish)
        return None

    sushi = _local_recipe("寿司", score=0.9)
    named = _local_recipe("家常卤面", score=0.2)

    def fake_search(query, **_kwargs):
        assert query == "卤面"
        return graph.ToolResult(
            status="ok", data={"evidence": [sushi, named]},
            evidence_ids=[sushi["id"], named["id"]],
        )

    monkeypatch.setattr(graph, "search_retrieval", fake_search)
    monkeypatch.setattr(graph, "lookup_recipe_tutorial", fail_fallback)

    class MustNotCallModel:
        async def chat_with_tools(self, **_kwargs):
            raise AssertionError("Named-dish flow must be server-rendered")

    result = graph.compute(SessionLocal, snapshot)

    assert calls == []
    assert "家常卤面" in result["text"]
    assert "寿司" not in result["text"]
    assert "做法来源：本地菜谱" in result["text"]
    assert "未核实" not in result["text"]


def test_named_dish_uses_retrieved_recipe_with_unrelated_title(monkeypatch):
    _, _, snapshot = start(message="我今天中午想吃卤面")
    calls = []

    async def fail_fallback(dish, *, provider=None):
        calls.append(dish)
        return None

    sushi = _local_recipe("寿司", score=0.62)
    monkeypatch.setattr(
        graph, "search_retrieval",
        lambda query, **_kwargs: graph.ToolResult(
            status="ok", data={"evidence": [sushi]}, evidence_ids=[sushi["id"]],
        ),
    )
    monkeypatch.setattr(graph, "lookup_recipe_tutorial", fail_fallback)

    class MustNotCallModel:
        async def chat_with_tools(self, **_kwargs):
            raise AssertionError("Named-dish flow must be server-rendered")

    result = graph.compute(SessionLocal, snapshot)

    assert calls == []
    assert "「卤面」对应《寿司》" in result["text"]
    assert "做法来源：本地菜谱" in result["text"]
    assert "未核实" not in result["text"]


def test_inventory_question_answers_yes_from_active_lot():
    actor, _, snapshot = start(message="冰箱里还有鸡蛋吗？")
    with SessionLocal.begin() as session:
        session.execute(delete(InventoryLot).where(InventoryLot.workspace_id == actor[1]))
        egg = session.scalar(select(Ingredient).where(Ingredient.key == "egg"))
        session.add(InventoryLot(
            workspace_id=actor[1],
            ingredient_id=egg.id,
            ingredient_key=egg.key,
            name=egg.name,
            quantity_status="known",
            quantity=Decimal("6"),
            unit="个",
            confirmed_at=datetime.now(UTC),
            source_type="test",
            source_id="inventory-yes",
            status="active",
        ))

    class MustNotCallModel:
        async def chat_with_tools(self, **_kwargs):
            raise AssertionError("Inventory questions must be server-rendered")

    result = graph.compute(SessionLocal, snapshot)

    assert result["status"] == "succeeded"
    assert result["text"] == "有。鸡蛋 6个。"
    assert result["citations"] == []
    assert result["_state"]["budgets"] == {"tool_calls": 1, "model_calls": 1}


def test_inventory_question_answers_no_without_active_lot():
    actor, _, snapshot = start(message="冰箱里还有鸡蛋吗？")
    with SessionLocal.begin() as session:
        session.execute(delete(InventoryLot).where(InventoryLot.workspace_id == actor[1]))

    class MustNotCallModel:
        async def chat_with_tools(self, **_kwargs):
            raise AssertionError("Inventory questions must be server-rendered")

    result = graph.compute(SessionLocal, snapshot)

    assert result["status"] == "succeeded"
    assert result["text"] == "没有。当前库存没有鸡蛋。"
    assert result["citations"] == []


def test_inventory_question_marks_unknown_quantity():
    actor, _, snapshot = start(message="冰箱里还有鸡蛋吗？")
    with SessionLocal.begin() as session:
        session.execute(delete(InventoryLot).where(InventoryLot.workspace_id == actor[1]))
        egg = session.scalar(select(Ingredient).where(Ingredient.key == "egg"))
        session.add(InventoryLot(
            workspace_id=actor[1],
            ingredient_id=egg.id,
            ingredient_key=egg.key,
            name=egg.name,
            quantity_status="unknown",
            quantity=None,
            unit=None,
            confirmed_at=datetime.now(UTC),
            source_type="test",
            source_id="inventory-unknown",
            status="active",
        ))

    class MustNotCallModel:
        async def chat_with_tools(self, **_kwargs):
            raise AssertionError("Inventory questions must be server-rendered")

    result = graph.compute(SessionLocal, snapshot)

    assert result["text"] == "有。鸡蛋的数量待核对。"


def test_inventory_question_treats_zero_quantity_as_empty():
    actor, _, snapshot = start(message="冰箱里还有鸡蛋吗？")
    with SessionLocal.begin() as session:
        session.execute(delete(InventoryLot).where(InventoryLot.workspace_id == actor[1]))
        egg = session.scalar(select(Ingredient).where(Ingredient.key == "egg"))
        session.add(InventoryLot(
            workspace_id=actor[1],
            ingredient_id=egg.id,
            ingredient_key=egg.key,
            name=egg.name,
            quantity_status="known",
            quantity=Decimal("0"),
            unit="个",
            confirmed_at=datetime.now(UTC),
            source_type="test",
            source_id="inventory-zero",
            status="active",
        ))

    class MustNotCallModel:
        async def chat_with_tools(self, **_kwargs):
            raise AssertionError("Inventory questions must be server-rendered")

    result = graph.compute(SessionLocal, snapshot)

    assert result["text"] == "没有。当前库存没有鸡蛋。"


def test_inventory_question_detection_skips_other_intents():
    names = ["鸡蛋", "番茄", "盐"]
    assert graph.inventory_question_names("冰箱里还有鸡蛋吗？", names) == ["鸡蛋"]
    assert graph.inventory_question_names("有没有番茄", names) == ["番茄"]
    assert graph.inventory_question_names("鸡蛋还有吗", names) == ["鸡蛋"]
    assert graph.inventory_question_names("鸡蛋够不够做蛋糕", names) == []
    assert graph.inventory_question_names("冰箱里有什么", names) == []
    assert graph.inventory_question_names("鸡蛋怎么做", names) == []


def test_base_prompt_requires_definitive_inventory_answers():
    assert "禁止改成“无法确认”" in graph.BASE_SYSTEM_PROMPT
    assert "不能断言冰箱里没有" not in graph.BASE_SYSTEM_PROMPT


def test_named_dish_short_quantity_joins_procurement_draft(monkeypatch):
    actor, _, snapshot = start(message="我今天中午想吃番茄炒蛋")
    recipe = {
        "id": "recipe:short-tomato:v1",
        "kind": "recipe",
        "recipe_id": "short-tomato",
        "title": "番茄炒蛋",
        "content_version": 1,
        "content": {
            "name": "番茄炒蛋",
            "category": "家常菜",
            "servings": 2,
            "ingredients": [
                {"name": "番茄", "ingredient_key": "tomato", "quantity": "1", "unit": "kg"},
                {"name": "鸡蛋", "ingredient_key": "egg", "quantity": "2", "unit": "个"},
            ],
            "steps": ["炒熟"],
            "allergens": [],
            "capabilities": {"quantities": True},
        },
    }
    monkeypatch.setattr(
        graph,
        "search_retrieval",
        lambda *_args, **_kwargs: graph.ToolResult(
            status="ok", data={"evidence": [recipe]}, evidence_ids=[recipe["id"]],
        ),
    )
    with SessionLocal.begin() as session:
        session.execute(delete(InventoryLot).where(InventoryLot.workspace_id == actor[1]))
        session.execute(delete(HouseholdProfile).where(HouseholdProfile.workspace_id == actor[1]))
        tomato = session.scalar(select(Ingredient).where(Ingredient.key == "tomato"))
        egg = session.scalar(select(Ingredient).where(Ingredient.key == "egg"))
        for row, quantity, unit, source in (
            (tomato, Decimal("0.5"), "kg", "short-tomato"),
            (egg, Decimal("6"), "个", "enough-egg"),
        ):
            session.add(InventoryLot(
                workspace_id=actor[1],
                ingredient_id=row.id,
                ingredient_key=row.key,
                name=row.name,
                quantity_status="known",
                quantity=quantity,
                unit=unit,
                confirmed_at=datetime.now(UTC),
                source_type="test",
                source_id=source,
                status="active",
            ))

    class MustNotCallModel:
        async def chat_with_tools(self, **_kwargs):
            raise AssertionError("Named-dish flow must be server-rendered")

    result = graph.compute(SessionLocal, snapshot)
    assert result["status"] == "waiting_confirmation"
    assert "用量不足：番茄" in result["text"]
    assert "已把用量不足的番茄整理成采购清单草稿" in result["text"]
    assert "要加入采购吗" not in result["text"]
    action = result["action"]
    assert action["type"] == "add_procurement"
    items = action["payload"]["items"]
    assert [item["name"] for item in items] == ["番茄"]
    assert items[0]["quantity_status"] == "unknown"
    assert items[0]["quantity"] is None and items[0]["unit"] is None
    assert items[0]["required"] == "1kg"
    assert items[0]["available"] == "0.5kg"
    assert items[0]["note"] == "菜谱所需：1kg，库存 0.5kg"
    runtime.validate_action("add_procurement", action["payload"])


def test_named_dish_unknown_quantity_stays_out_of_procurement_draft(monkeypatch):
    actor, _, snapshot = start(message="我今天中午想吃番茄炒蛋")
    recipe = {
        "id": "recipe:unknown-tomato:v1",
        "kind": "recipe",
        "recipe_id": "unknown-tomato",
        "title": "番茄炒蛋",
        "content_version": 1,
        "content": {
            "name": "番茄炒蛋",
            "category": "家常菜",
            "servings": 2,
            "ingredients": [
                {"name": "番茄", "ingredient_key": "tomato", "quantity": "1", "unit": "kg"},
            ],
            "steps": ["炒熟"],
            "allergens": [],
            "capabilities": {"quantities": True},
        },
    }
    monkeypatch.setattr(
        graph,
        "search_retrieval",
        lambda *_args, **_kwargs: graph.ToolResult(
            status="ok", data={"evidence": [recipe]}, evidence_ids=[recipe["id"]],
        ),
    )
    with SessionLocal.begin() as session:
        session.execute(delete(InventoryLot).where(InventoryLot.workspace_id == actor[1]))
        session.execute(delete(HouseholdProfile).where(HouseholdProfile.workspace_id == actor[1]))
        tomato = session.scalar(select(Ingredient).where(Ingredient.key == "tomato"))
        session.add(InventoryLot(
            workspace_id=actor[1],
            ingredient_id=tomato.id,
            ingredient_key=tomato.key,
            name=tomato.name,
            quantity_status="unknown",
            quantity=None,
            unit=None,
            confirmed_at=datetime.now(UTC),
            source_type="test",
            source_id="unknown-tomato",
            status="active",
        ))

    class MustNotCallModel:
        async def chat_with_tools(self, **_kwargs):
            raise AssertionError("Named-dish flow must be server-rendered")

    result = graph.compute(SessionLocal, snapshot)
    assert result["status"] == "waiting_input"
    assert "库存数量待核对：番茄" in result["text"]
    assert "暂未加入采购草稿" in result["text"]
    assert "要加入采购候选吗" not in result["text"]
    assert "action" not in result


def test_named_dish_worker_creates_one_batch_procurement_draft(monkeypatch):
    with SessionLocal() as session:
        member = session.scalar(select(WorkspaceMember))
        actor = member.user_id, member.workspace_id
    with SessionLocal.begin() as session:
        session.execute(delete(InventoryLot).where(InventoryLot.workspace_id == actor[1]))
        session.execute(
            delete(HouseholdProfile).where(HouseholdProfile.workspace_id == actor[1])
        )
        session.execute(delete(ShoppingItem).where(ShoppingItem.workspace_id == actor[1]))

    recipe = {
        "id": "recipe:worker-noodle:v1",
        "kind": "recipe",
        "recipe_id": "worker-noodle",
        "title": "卤面",
        "content_version": 1,
        "content": {
            "name": "卤面",
            "category": "主食",
            "servings": 2,
            "ingredients": [
                {"name": "五花肉", "ingredient_key": "pork_belly"},
                {"name": "芹菜", "ingredient_key": "celery"},
            ],
            "steps": ["煮熟"],
            "allergens": [],
            "capabilities": {"quantities": False},
        },
    }
    monkeypatch.setattr(
        graph,
        "search_retrieval",
        lambda *_args, **_kwargs: graph.ToolResult(
            status="ok", data={"evidence": [recipe]}, evidence_ids=[recipe["id"]],
        ),
    )

    response = runtime.create_turn(*actor, None, "我想吃卤面")
    worker.execute(worker.claim("named-dish-worker"), "named-dish-worker")
    run = runtime.accessible_run(actor[0], response["run_id"])
    assert run.status == "waiting_confirmation"
    assert "要我把缺少的" not in run.result["text"]
    assert "action" not in run.result
    action = run.result["pending_action"]
    assert action["type"] == "add_procurement"
    assert [item["name"] for item in action["payload"]["items"]] == ["五花肉", "芹菜"]

    decided = runtime.decide_action(actor[0], action["id"], True, "named-batch")
    assert decided["status"] == "confirmed"
    with SessionLocal() as session:
        items = session.scalars(
            select(ShoppingItem).where(ShoppingItem.workspace_id == actor[1])
        ).all()
        assert {item.name for item in items} == {"五花肉", "芹菜"}
        assert all(item.quantity_status == "unknown" for item in items)
        assert session.scalar(
            select(func.count()).select_from(InventoryLot).where(
                InventoryLot.workspace_id == actor[1]
            )
        ) == 0


def test_inventory_tool_only_reads_active_lots():
    actor, _, snapshot = start()
    with SessionLocal.begin() as session:
        session.execute(delete(InventoryLot).where(InventoryLot.workspace_id == actor[1]))
        tomato = session.scalar(select(Ingredient).where(Ingredient.key == "tomato"))
        egg = session.scalar(select(Ingredient).where(Ingredient.key == "egg"))
        for status, name, row in (
            ("active", "活跃番茄", tomato), ("consumed", "已消耗鸡蛋", egg),
        ):
            session.add(InventoryLot(
                workspace_id=actor[1],
                ingredient_id=row.id,
                ingredient_key=row.key,
                name=name,
                quantity_status="known",
                quantity=Decimal("1"),
                unit=row.default_unit,
                confirmed_at=datetime.now(UTC),
                source_type="test",
                source_id=f"inventory-tool-{status}",
                status=status,
            ))
    result = asyncio.run(
        graph.Runtime(SessionLocal, snapshot).inventory(graph.QueryInput(query="库存"))
    )
    assert [item["name"] for item in result.data["evidence"]] == ["活跃番茄"]


def test_restricted_food_direction_options_use_inventory_and_follow_up_returns_steps():
    from app.db.models import Ingredient

    actor, response, snapshot = start(message="我想吃虾了")
    seed_recipes(SessionLocal, actor[1])
    seed_direction_recipes(SessionLocal, actor[1])
    with SessionLocal.begin() as session:
        session.add(HouseholdProfile(
            workspace_id=actor[1], display_name="我", preferences=[],
            restrictions=[{
                "type": "allergy", "ingredient_key": "shrimp", "ingredient_name": "虾仁",
            }],
        ))
        tomato = session.scalar(select(Ingredient).where(Ingredient.key == "tomato"))
        session.add(InventoryLot(
            workspace_id=actor[1], ingredient_id=tomato.id, ingredient_key=tomato.key,
            name=tomato.name, quantity_status="unknown", quantity=None, unit=None,
            notes="", confirmed_at=datetime.now(UTC), source_type="test",
            source_id="test-tomato",
        ))
    worker.execute(snapshot.job_id, "graph-worker")
    first = runtime.accessible_run(actor[0], response["run_id"])
    assert first.status == "succeeded"
    assert "我按现有库存" in first.result["text"]
    options = first.result["options"]
    assert 2 <= len(options) <= 3
    labels = {option["label"] for option in options}
    by_key = {option["key"]: option for option in options}
    assert {"清淡蒸煮", "红烧酱香"} <= labels
    assert by_key["braised"]["example"] == "红烧茄子"
    assert by_key["sour_spicy"]["missing"] == ["鸡蛋"]
    assert all(option["example"] not in {"红烧酱汁", "金枪鱼酱三明治"} for option in options)
    assert "pending_action" not in first.result
    assert all("库存已有番茄" in option["reason"] for option in options)
    assert all("数量待核对" in option["reason"] for option in options)
    assert all(option["example"] for option in options)
    assert not any(step in first.result["text"] for step in ("蒸熟", "烧熟", "炒熟"))

    selected = next(option for option in options if option["label"] == "清淡蒸煮")
    assert selected["example"] == "清蒸鲈鱼"
    _, second_response, second_snapshot = start(
        chat_id=first.session_id, message=selected["prompt"], actor=actor,
    )
    worker.execute(second_snapshot.job_id, "graph-worker")
    second = runtime.accessible_run(actor[0], second_response["run_id"])
    assert second.status == "succeeded"
    assert not second.result.get("options")
    assert "按「清淡蒸煮」方向" in second.result["text"]
    assert "蒸熟" in second.result["text"]
    assert "[recipe:" not in second.result["text"]


def test_direction_parser_requires_explicit_choice_and_ignores_ingredient_hints():
    assert graph._direction_from_text("我想吃虾，不要辣") is None
    assert graph._direction_from_text("我想吃虾，不要辣的口味") is None
    assert graph._direction_from_text("按「清淡蒸煮」方向推荐")["key"] == "light"
    directions = {item["key"]: item for item in graph._COOKING_DIRECTIONS}
    recipe = {
        "name": "红烧茄子", "category": "家常菜", "tags": [], "taste_tags": [],
        "ingredients": [{"name": "番茄"}],
    }
    assert graph._recipe_direction_score(recipe, directions["braised"]) == 1
    assert graph._recipe_direction_score(recipe, directions["sour_spicy"]) == 0
    sandwich = {
        "name": "金枪鱼酱三明治", "category": "早餐", "tags": [], "taste_tags": [],
        "ingredients": [{"name": "番茄"}],
    }
    assert graph._recipe_direction_score(sandwich, directions["braised"]) == 0


def test_similar_food_terms_prefers_same_group_without_restricted_alias():
    shrimp_terms = graph.similar_food_terms("虾仁")
    assert {"鱼", "紫菜"} <= shrimp_terms
    assert "虾" not in shrimp_terms and "虾仁" not in shrimp_terms
    assert {"核桃", "腰果"} <= graph.similar_food_terms("花生")
    assert graph.similar_food_terms("番茄") == set()


def test_recipe_search_filters_shrimp_aliases_and_retains_only_readable_evidence(monkeypatch):
    from app.agent.tool_runtime import QueryInput, ToolResult

    _, _, snapshot = start()
    engine = graph.Runtime(SessionLocal, snapshot)
    engine.state = {"constraints": {"excluded_ingredients": ["虾仁"]}}
    blocked = {"id": "recipe:shrimp:v1", "content": {"ingredients": ["鲜虾"]}}
    allowed = {"id": "recipe:tomato:v1", "content": {"ingredients": ["番茄"]}}
    monkeypatch.setattr(graph, "search_retrieval", lambda *args, **kwargs: ToolResult(
        status="ok", data={"evidence": [blocked, allowed], "mode": "demo"},
        evidence_ids=[blocked["id"], allowed["id"]],
    ))
    result = asyncio.run(engine.search_recipes(QueryInput(query="虾")))
    assert result.data["evidence"] == [allowed]
    assert result.evidence_ids == [allowed["id"]]
    monkeypatch.setattr(graph, "search_retrieval", lambda *args, **kwargs: ToolResult(
        status="ok", data={"evidence": [blocked]}, evidence_ids=[blocked["id"]],
    ))
    result = asyncio.run(engine.search_recipes(QueryInput(query="虾")))
    assert result.status == "no_result"
    assert result.data == {"evidence": [], "blocked_ingredients": ["虾仁"]}
    assert result.evidence_ids == []


def test_timeout_is_run_wide_and_not_reset_on_retry():
    actor, response, snapshot = start()
    with SessionLocal.begin() as session:
        session.get(TurnRun, snapshot.run_id).created_at = runtime.utc_now() - timedelta(seconds=61)
    worker.execute(snapshot.job_id, "graph-worker")
    run = runtime.accessible_run(actor[0], response["run_id"])
    assert run.status == "failed" and run.error_code == "RUN_TIMEOUT"
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ToolExecution)) == 0


def test_committed_tool_result_reused_after_graph_node_crash(monkeypatch):
    _, _, snapshot = start()
    instance = graph.Runtime(SessionLocal, snapshot)
    state = instance.context({})
    state.update(instance.decide(state))
    original = graph.dispatch
    calls = []

    async def counted(tool, arguments):
        calls.append(tool.name)
        return await original(tool, arguments)

    monkeypatch.setattr(graph, "dispatch", counted)
    first = instance.execute_tools(state)
    # Simulate death after the tool transaction commits but before its node checkpoint.
    recovered = graph.Runtime(SessionLocal, snapshot).execute_tools(state)
    assert recovered["messages"] == first["messages"]
    assert calls == ["get_member_constraints"]


def test_read_result_reused_across_call_ids_and_revoked_facts_rejected(monkeypatch):
    actor, _, snapshot = start()
    instance = graph.Runtime(SessionLocal, snapshot)
    state = instance.context({})
    state.update(instance.decide(state))
    instance.execute_tools(state)

    async def forbidden(*args):
        pytest.fail("Same read arguments and facts must reuse the committed execution")

    monkeypatch.setattr(graph, "dispatch", forbidden)
    state["calls"][0]["id"] = "another-provider-call-id"
    reused = instance.execute_tools(state)
    assert reused["messages"][-1]["tool_call_id"] == "another-provider-call-id"
    with SessionLocal.begin() as session:
        session.add(
            HouseholdProfile(
                workspace_id=actor[1],
                display_name="new restriction",
                restrictions=["牛奶"],
                preferences=[],
            )
        )
    with pytest.raises(graph.ExecutionStopped, match="FACTS_CHANGED"):
        instance.execute_tools(state)


def test_model_budget_reserved_before_failure_survives_new_runtime():
    class Failing:
        async def chat_with_tools(self, **kwargs):
            raise RuntimeError("offline injected failure")

    _, _, snapshot = start()
    for _ in range(graph.MAX_MODEL_CALLS):
        instance = graph.Runtime(SessionLocal, snapshot, Failing())
        with pytest.raises(RuntimeError, match="offline injected"):
            instance.decide(instance.context({}))
    instance = graph.Runtime(SessionLocal, snapshot, Failing())
    result = instance.decide(instance.context({}))
    assert result["response"]["error_code"] == "BUDGET_EXHAUSTED"


def test_tool_return_after_cancellation_cannot_persist(monkeypatch):
    actor, _, snapshot = start()
    instance = graph.Runtime(SessionLocal, snapshot)
    state = instance.context({})
    state.update(instance.decide(state))

    async def cancel_before_return(*args):
        runtime.cancel_run(actor[0], snapshot.run_id)
        return graph.ToolResult(status="ok")

    monkeypatch.setattr(graph, "dispatch", cancel_before_return)
    with pytest.raises(graph.ExecutionStopped, match="RUN_STOPPED"):
        instance.execute_tools(state)
    with SessionLocal() as session:
        records = session.scalars(select(ToolExecution)).all()
        assert len(records) == 1 and records[0].status == "running" and records[0].result == {}


def test_worker_total_deadline_stops_blocked_computation(monkeypatch):
    from threading import Event

    actor, response, snapshot = start()
    release = Event()

    def blocked(_snapshot):
        release.wait(5)
        return {"status": "succeeded", "action": {"type": "add_procurement", "payload": {}}}

    monkeypatch.setattr(worker, "remaining_seconds", lambda _: 0.03)
    monkeypatch.setattr(worker, "compute_result", blocked)
    try:
        worker.execute(snapshot.job_id, "graph-worker")
        run = runtime.accessible_run(actor[0], response["run_id"])
        assert run.error_code == "RUN_TIMEOUT"
        assert run.status == "failed"
        with SessionLocal() as session:
            assert session.get(BackgroundJob, snapshot.job_id).status == "done"
    finally:
        release.set()


def test_postgres_saver_survives_new_runtime_and_fences_stale_attempt(
    postgres_runtime,  # noqa: F811
    monkeypatch,
):
    engine, sessions, actor = postgres_runtime
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    _, response, snapshot = start(sessions, actor=actor, message="买点鸡蛋")
    original = graph.Runtime.validate
    attempts = []

    def crash(self, state):
        attempts.append(True)
        raise RuntimeError("crash before final checkpoint")

    monkeypatch.setattr(graph.Runtime, "validate", crash)
    with pytest.raises(RuntimeError, match="crash before"):
        graph.compute(sessions, snapshot)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ToolExecution)) == 2
    schema = engine.get_execution_options()["schema_translate_map"][None]
    with engine.connect() as connection:
        assert connection.scalar(text(f'SELECT count(*) FROM "{schema}".checkpoints')) > 0
    monkeypatch.setattr(graph.Runtime, "validate", original)
    with sessions.begin() as session:
        session.get(BackgroundJob, snapshot.job_id).lease_until = runtime.utc_now() - timedelta(
            seconds=1
        )
    assert worker.claim("new-worker") == snapshot.job_id
    fresh = worker.snapshot_job(snapshot.job_id, "new-worker")
    with pytest.raises(graph.ExecutionStopped, match="LEASE_LOST"):
        graph.compute(sessions, snapshot)
    result = graph.compute(sessions, fresh)
    assert result["status"] == "waiting_confirmation"
    assert result["_state"]["budgets"]["tool_calls"] == 2
    worker.finish(fresh, result)
    assert runtime.accessible_run(actor[0], response["run_id"]).status == "waiting_confirmation"
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ToolExecution)) == 2


@pytest.fixture(params=["postgres"])
def graph_database(request, monkeypatch):
    if request.param == "postgres":
        _engine, sessions, actor = request.getfixturevalue("postgres_runtime")
        monkeypatch.setattr(worker, "SessionLocal", sessions)
        return sessions, actor
    with SessionLocal() as session:
        member = session.scalar(select(WorkspaceMember))
        return SessionLocal, (member.user_id, member.workspace_id)


def test_light_menu_without_light_candidates_requests_llm_fallback(graph_database):
    sessions, actor = graph_database
    seed_recipes(sessions, actor[1])
    _, _, snapshot = start(sessions, actor=actor, message="两人清淡菜单")
    instance = graph.Runtime(sessions, snapshot)
    instance.state = instance.context({})

    assert instance.state["constraints"]["diners"] == 2
    assert instance.state["constraints"]["dietary_style"] == ["清淡"]
    result = asyncio.run(instance.menu(graph.QueryInput(query="两人清淡菜单")))
    assert result.status == "no_result"
    assert result.data["fallback_allowed"] is True
    assert result.data["dietary_style"] == ["清淡"]


def test_light_menu_returns_only_light_candidates(graph_database):
    sessions, actor = graph_database
    seed_direction_recipes(sessions, actor[1])
    _, _, snapshot = start(sessions, actor=actor, message="两人清淡菜单")
    instance = graph.Runtime(sessions, snapshot)
    instance.state = instance.context({})

    result = asyncio.run(instance.menu(graph.QueryInput(query="两人清淡菜单")))
    assert result.status == "ok"
    assert [row["name"] for row in result.data["menu_draft"]] == ["清蒸鲈鱼"]


def test_menu_taste_fallback_keeps_llm_answer_and_marks_unverified(graph_database):
    sessions, actor = graph_database
    seed_recipes(sessions, actor[1])
    _, _, snapshot = start(sessions, actor=actor, message="两人清淡菜单")

    class TasteProvider:
        async def chat_with_tools(self, *, messages, tools):
            if not any(message["role"] == "tool" for message in messages):
                return {
                    "tool_calls": [{
                        "id": "menu",
                        "type": "function",
                        "function": {
                            "name": "recommend_menu",
                            "arguments": json.dumps({"query": "两人清淡菜单"}),
                        },
                    }],
                }
            return {
                "content": "推荐清蒸鱼、白灼菜心和番茄蛋汤。",
                "tool_calls": [],
            }

    result = graph.compute(sessions, snapshot, TasteProvider())
    assert result["status"] == "succeeded"
    assert "推荐清蒸鱼、白灼菜心和番茄蛋汤。" in result["text"]
    assert "模型补充 · 未核实" in result["text"]
    assert result["_state"]["menu_draft"] == []


def test_s4_structured_restrictions_shared_menu_and_clear(graph_database):
    sessions, actor = graph_database
    seed_recipes(sessions, actor[1])
    with sessions.begin() as session:
        profile = session.scalar(select(HouseholdProfile))
        profile.restrictions = [
            {"type": "allergy", "ingredient_key": "peanut"},
            {"type": "legacy_free_text", "text": "不吃鸡蛋"},
        ]
        session.add(HouseholdProfile(
            workspace_id=actor[1], display_name="家庭共享限制", linked_user_id=None,
            restrictions=[{"type": "dislike", "ingredient_key": "celery"}],
            preferences=[],
        ))
    _, response, snapshot = start(sessions, actor=actor, message="三人菜单")
    instance = graph.Runtime(sessions, snapshot)
    instance.state = instance.context({})
    with sessions() as session:
        peanut_recipe = next(
            doc.recipe_id for doc in session.scalars(select(KnowledgeDocument))
            if "花生" in doc.content["ingredients"]
        )
    availability = asyncio.run(instance.availability(graph.RecipeInput(recipe_id=peanut_recipe)))
    assert availability.data["conflicts"] == ["peanut"]
    assert availability.data["quantity_sufficient"] is None
    worker.execute(snapshot.job_id, "graph-worker")
    first = runtime.accessible_run(actor[0], response["run_id"])
    assert first.status == "succeeded"
    assert len(first.result["menu_draft"]) == 3
    assert first.state["constraints"]["excluded_ingredients"] == ["celery", "peanut", "鸡蛋"]
    assert all("花生" not in row["content"]["ingredients"] for row in first.result["menu_draft"])
    _, response, snapshot = start(
        sessions, actor=actor, chat_id=first.session_id, message="第二道换一道"
    )
    worker.execute(snapshot.job_id, "graph-worker")
    second = runtime.accessible_run(actor[0], response["run_id"])
    before, after = first.result["menu_draft"], second.result["menu_draft"]
    assert before[0] == after[0] and before[2] == after[2] and before[1] != after[1]
    _, response, snapshot = start(
        sessions, actor=actor, chat_id=second.session_id,
        message="取消本次忌口，两人菜单，花生可以吃",
    )
    worker.execute(snapshot.job_id, "graph-worker")
    third = runtime.accessible_run(actor[0], response["run_id"])
    assert third.state["constraints"] == {
        "diners": 2, "excluded_ingredients": ["celery", "peanut", "鸡蛋"]
    }


def test_s4_events_only_apply_within_inclusive_dates(graph_database, monkeypatch):
    sessions, actor = graph_database
    seed_recipes(sessions, actor[1])
    with sessions.begin() as session:
        profile = session.scalar(select(HouseholdProfile))
        profile.restrictions = [{"type": "allergy", "ingredient_key": "peanut"}]
        profile.legacy_details = {"events": [
            {"id": "active", "event_type": "感冒", "constraint_tags": ["忌辣", "宜清淡"],
             "start_date": "2026-11-01", "end_date": "2026-11-03"},
            {"id": "future", "start_date": "2026-11-05", "end_date": "2026-11-06"},
            {"id": "expired", "start_date": "2026-10-01", "end_date": "2026-10-02"},
        ]}
        for doc in session.scalars(select(KnowledgeDocument)):
            doc.content = {**doc.content, "tags": ["辣"]}
    _, _, snapshot = start(sessions, actor=actor, message="三人菜单")
    instance = graph.Runtime(sessions, snapshot)
    clock = [datetime(2026, 11, 1, tzinfo=UTC)]
    monkeypatch.setattr(graph, "utc_now", lambda: clock[0])
    for day in (1, 3):
        clock[0] = datetime(2026, 11, day, tzinfo=UTC)
        with sessions.begin() as session:
            session.get(TurnRun, snapshot.run_id).created_at = clock[0]
        instance.state = instance.context({})
        members = asyncio.run(instance.members(graph.QueryInput(query="成员限制")))
        assert [e["id"] for e in members.data["profiles"][0]["active_events"]] == ["active"]
        assert asyncio.run(instance.menu(graph.QueryInput(query="三人菜单"))).status == "no_result"
    previous = instance.state
    clock[0] = datetime(2026, 11, 4, tzinfo=UTC)
    with sessions.begin() as session:
        session.get(TurnRun, snapshot.run_id).created_at = clock[0]
    with pytest.raises(graph.ExecutionStopped, match="FACTS_CHANGED"):
        instance.check_facts(previous)
    instance.state = instance.context({})
    assert instance.state["member_context"][0]["active_events"] == []
    assert instance.state["constraints"]["excluded_ingredients"] == ["peanut"]
    assert asyncio.run(instance.menu(graph.QueryInput(query="三人菜单"))).status == "ok"
    with sessions.begin() as session:
        profile = session.scalar(select(HouseholdProfile))
        profile.legacy_details = {"events": [*profile.legacy_details["events"], {
            "id": "new", "start_date": "2026-11-04", "end_date": "2026-11-04",
            "constraint_tags": ["忌辣"],
        }]}
    with pytest.raises(graph.ExecutionStopped, match="FACTS_CHANGED"):
        instance.check_facts(instance.state)
    with sessions() as session:
        profile = session.scalar(select(HouseholdProfile))
        assert profile.restrictions == [{"type": "allergy", "ingredient_key": "peanut"}]
        assert len(profile.legacy_details["events"]) == 4


def test_preference_tool_requires_confirmation_and_preserves_allergy(graph_database):
    sessions, actor = graph_database
    with sessions.begin() as session:
        profile = HouseholdProfile(
            workspace_id=actor[1], display_name="孩子",
            restrictions=[{"type": "allergy", "ingredient_key": "peanut"}], preferences=[],
        )
        session.add(profile)
        session.flush()
        profile_id = profile.id

    class PreferenceProvider:
        async def chat_with_tools(self, *, messages, tools):
            completed = [m["name"] for m in messages if m["role"] == "tool"]
            sequence = [
                "get_member_constraints", "get_preference_summary", "prepare_preference_action"
            ]
            if len(completed) == len(sequence):
                return {"content": "请确认长期偏好", "citations": []}
            name = sequence[len(completed)]
            arguments = {"query": "成员偏好"} if name != "prepare_preference_action" else {
                "profile_id": profile_id, "preference": "喜欢清淡",
            }
            return {"tool_calls": [{"id": name, "type": "function", "function": {
                "name": name, "arguments": json.dumps(arguments),
            }}]}

    _, response, snapshot = start(sessions, actor=actor, message="记住孩子喜欢清淡")
    result = graph.compute(sessions, snapshot, PreferenceProvider())
    assert result["status"] == "waiting_confirmation"
    worker.finish(snapshot, result)
    run = runtime.accessible_run(actor[0], response["run_id"])
    with sessions() as session:
        assert session.get(HouseholdProfile, profile_id).preferences == []
    action_id = run.result["pending_action"]["id"]
    confirmed = runtime.decide_action(actor[0], action_id, True, "preference-confirm")
    assert runtime.decide_action(actor[0], action_id, True, "preference-confirm") == confirmed
    with sessions() as session:
        profile = session.get(HouseholdProfile, profile_id)
        assert profile.preferences == ["喜欢清淡"]
        assert profile.restrictions == [{"type": "allergy", "ingredient_key": "peanut"}]
        assert profile.version == 2


@pytest.mark.parametrize(
    "term,ingredient", [("peanut", "花生"), ("鸡蛋", "egg"), ("celery", "芹菜")]
)
def test_availability_and_menu_share_ingredient_matching(term, ingredient):
    state = {"constraints": {"excluded_ingredients": [term]},
             "ingredient_names": {"celery": "芹菜"}}
    assert graph.recipe_conflicts({"ingredients": [ingredient]}, state) == [term]


def test_cooking_logs_are_citable_only_after_explicit_opt_in():
    from app.services import cooking_logs as cooking_log_service

    actor, response, snapshot = start(message="上次那道菜怎么样")
    with SessionLocal() as session:
        hidden = cooking_log_service.create_log(
            session,
            workspace_id=actor[1],
            creator_id=actor[0],
            title="默认不引用",
            cooked_at=datetime.now(UTC),
            notes="家庭自述",
            allow_assistant_citation=False,
            dishes=[{"name": "清蒸鱼"}],
        )
        visible = cooking_log_service.create_log(
            session,
            workspace_id=actor[1],
            creator_id=actor[0],
            title="允许引用",
            cooked_at=datetime.now(UTC),
            notes="家庭自述",
            allow_assistant_citation=True,
            dishes=[{"name": "番茄炒蛋"}],
        )

    class Citing:
        def __init__(self, target: str):
            self.target = target

        async def chat_with_tools(self, **kwargs):
            return {
                "content": f"上次做饭记录可参考 [log:{self.target}]",
                "citations": [f"log:{self.target}"],
            }

    denied = graph.compute(SessionLocal, snapshot, Citing(hidden["id"]))
    assert denied["error_code"] == "INVALID_CITATION"

    _, _, snapshot = start(message="再看一次记录", actor=actor)
    allowed = graph.compute(SessionLocal, snapshot, Citing(visible["id"]))
    assert allowed["status"] == "succeeded"
    assert f"log:{visible['id']}" in allowed["citations"]
    assert {item["id"] for item in allowed["evidence"]} == {f"log:{visible['id']}"}


def test_web_recipe_fallback_matches_household_restriction():
    actor, _, snapshot = start(message="网页菜谱能做吗")
    with SessionLocal.begin() as session:
        session.add(
            HouseholdProfile(
                workspace_id=actor[1],
                display_name="妈妈",
                restrictions=[
                    {
                        "type": "allergy",
                        "ingredient_key": "peanut",
                        "ingredient_name": "花生",
                    }
                ],
                preferences=[],
            )
        )
    instance = graph.Runtime(SessionLocal, snapshot)
    instance.state = instance.context({})
    matched = asyncio.run(
        instance.verify_web_recipe(
            graph.WebRecipeInput(
                title="花生炖猪蹄",
                source_url="https://example.com/peanut",
                ingredients=["猪蹄", "花生"],
            )
        )
    )
    assert matched.status == "ok"
    payload = matched.data["evidence"][0]["content"]
    assert payload["status"] == "matched"
    assert "不可推荐" in payload["line"]
    assert "安全" not in payload["line"]
    assert matched.evidence_ids[0].startswith("web:")

    clean = asyncio.run(
        instance.verify_web_recipe(
            graph.WebRecipeInput(
                title="番茄炒蛋",
                source_url="https://example.com/eggs",
                ingredients=["番茄", "鸡蛋"],
            )
        )
    )
    line = clean.data["evidence"][0]["content"]["line"]
    assert clean.data["evidence"][0]["content"]["status"] == "not_detected_unverified"
    assert "不能视为安全" in line and "通过" not in line


# Reuse the explicit opt-in, unique-schema PostgreSQL fixture; no app schema is touched.
from test_runtime_postgres_concurrency import postgres_runtime  # noqa: E402, F401
