import asyncio
import json

from app.services import recipe_fallback


class FakeProvider:
    def __init__(self, payload: dict):
        self.payload = payload
        self.messages: list[dict] = []

    async def chat(self, *, messages):
        self.messages = messages
        return json.dumps(self.payload, ensure_ascii=False)


def test_lookup_recipe_tutorial_normalizes_web_summary(monkeypatch):
    monkeypatch.setattr(
        recipe_fallback,
        "search_with_briefs",
        lambda keyword, limit: [{
            "title": "家常卤面",
            "url": "https://www.xiachufang.com/recipe/123/",
            "ingredients": "五花肉 芹菜 鲜面条",
            "steps_brief": ["炒肉", "蒸面"],
        }],
    )
    provider = FakeProvider({
        "title": "家常卤面",
        "servings": 2,
        "ingredients": [
            {"name": "五花肉", "amount": "150克"},
            {"name": "芹菜", "amount": "1把"},
            {"name": "鲜面条", "amount": "300克"},
        ],
        "steps": ["五花肉切片。", "面条蒸熟后拌匀。"],
    })

    content = asyncio.run(recipe_fallback.lookup_recipe_tutorial("卤面", provider=provider))

    assert content is not None
    assert content["name"] == "家常卤面"
    assert content["ingredients"][0] == {
        "name": "五花肉",
        "ingredient_key": "",
        "amount": "150克",
        "optional": False,
    }
    assert content["steps"] == ["五花肉切片。", "面条蒸熟后拌匀。"]
    assert content["source_url"] == "https://www.xiachufang.com/recipe/123/"
    assert content["source_name"] == "网络教程"
    assert content["capabilities"] == {"quantities": False}
    assert content["unverified"] is True
    assert "家常卤面" in provider.messages[1]["content"]


def test_lookup_recipe_tutorial_cleans_polluted_ingredient_names(monkeypatch):
    monkeypatch.setattr(recipe_fallback, "search_with_briefs", lambda keyword, limit: [])
    provider = FakeProvider({
        "title": "反沙芋头",
        "ingredients": [
            {"name": "荔浦芋头、实惠新鲜）", "amount": "500克"},
            {"name": "白砂糖或冰糖", "amount": "30克"},
            {"name": "盆", "amount": ""},
        ],
        "steps": ["芋头切条。", "炸至金黄后反沙。"],
    })

    content = asyncio.run(
        recipe_fallback.lookup_recipe_tutorial("反沙芋头", provider=provider)
    )

    assert content is not None
    assert [item["name"] for item in content["ingredients"]] == ["荔浦芋头", "白砂糖或冰糖"]
    assert content["ingredients"][0]["amount"] == "500克"
    assert "不得包含括号" in provider.messages[0]["content"]


def test_lookup_recipe_tutorial_drops_parenthetical_note_fragments(monkeypatch):
    monkeypatch.setattr(recipe_fallback, "search_with_briefs", lambda keyword, limit: [])
    provider = FakeProvider({
        "title": "白菜拌芝麻",
        "ingredients": [
            {"name": "大白菜", "amount": ""},
            {"name": "冬季黄心白菜最佳）", "amount": ""},
            {"name": "熟白芝麻", "amount": ""},
            {"name": "用于点缀）", "amount": ""},
        ],
        "steps": ["洗净。", "装盘。"],
    })

    content = asyncio.run(
        recipe_fallback.lookup_recipe_tutorial("白菜拌芝麻", provider=provider)
    )

    assert content is not None
    assert [item["name"] for item in content["ingredients"]] == ["大白菜", "熟白芝麻"]


def test_lookup_recipe_tutorial_marks_model_only_output(monkeypatch):
    monkeypatch.setattr(recipe_fallback, "search_with_briefs", lambda keyword, limit: [])
    provider = FakeProvider({
        "title": "卤面",
        "ingredients": [{"name": "面条", "amount": "适量"}],
        "steps": ["准备面条。", "蒸熟。"],
    })

    content = asyncio.run(recipe_fallback.lookup_recipe_tutorial("卤面", provider=provider))

    assert content is not None
    assert content["source_url"] == ""
    assert content["source_name"] == "模型补充"
    assert content["fallback_kind"] == "llm"


def test_lookup_recipe_tutorial_skips_web_when_disabled(monkeypatch):
    def fail_search(*_args, **_kwargs):
        raise AssertionError("web search must stay off when disabled")

    monkeypatch.setattr(recipe_fallback, "search_with_briefs", fail_search)
    provider = FakeProvider({
        "title": "卤面",
        "ingredients": [{"name": "面条", "amount": "适量"}],
        "steps": ["准备面条。", "蒸熟。"],
    })

    content = asyncio.run(
        recipe_fallback.lookup_recipe_tutorial("卤面", provider=provider, web_enabled=False)
    )

    assert content is not None
    assert content["source_name"] == "模型补充"
    assert content["fallback_kind"] == "llm"
    assert "没有可用的网络搜索结果" in provider.messages[1]["content"]


def test_lookup_recipe_tutorial_ignores_tool_only_provider(monkeypatch):
    class ToolOnly:
        async def chat_with_tools(self, **_kwargs):
            raise AssertionError("fallback needs plain chat, not tool calls")

    fallback_provider = FakeProvider({
        "title": "卤面",
        "ingredients": [{"name": "面条", "amount": "适量"}],
        "steps": ["准备面条。", "蒸熟。"],
    })
    monkeypatch.setattr(recipe_fallback, "search_with_briefs", lambda keyword, limit: [])
    monkeypatch.setattr(
        recipe_fallback, "build_llm_provider", lambda _settings: fallback_provider,
    )

    content = asyncio.run(
        recipe_fallback.lookup_recipe_tutorial("卤面", provider=ToolOnly())
    )

    assert content is not None
    assert content["fallback_kind"] == "llm"


def test_lookup_recipe_tutorial_without_provider_stays_offline(monkeypatch):
    def fail_search(*_args, **_kwargs):
        raise AssertionError("no provider must not trigger any network request")

    monkeypatch.setattr(recipe_fallback, "search_with_briefs", fail_search)
    monkeypatch.setattr(recipe_fallback, "build_llm_provider", lambda _settings: None)

    assert asyncio.run(recipe_fallback.lookup_recipe_tutorial("卤面")) is None