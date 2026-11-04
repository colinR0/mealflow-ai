import httpx
import pytest

from app.core.config import Settings
from app.main import app


def _settings() -> Settings:
    return Settings(
        llm_api_key="test-default-key",
        llm_model="deepseek-v4-pro",
        bigmodel_api_key="test-bigmodel-key",
        bigmodel_text_model="glm-4.5-air",
        bigmodel_vision_model="glm-4.6v",
        llm_base_url="https://api.deepseek.com",
        aitechflux_api_key="test-gateway-key",
    )


@pytest.mark.asyncio
async def test_model_list_is_public_metadata_only(monkeypatch) -> None:
    monkeypatch.setattr("app.api.routes.chat.get_settings", _settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/agent/chat/models")
    assert response.status_code == 200
    assert len(response.json()) == 5
    assert response.json()[0]["default"] is True
    assert response.json()[0]["is_free"] is False
    assert response.json()[0]["label"] == "GLM-4.5-Air"
    assert response.json()[0]["use_case"] == "默认文本 Agent｜菜单规划、结构化意图与工具调用"
    assert response.json()[1]["label"] == "GLM-4.6V"
    assert "视觉任务" in response.json()[1]["use_case"]
    assert "test-gateway-key" not in response.text
    assert "test-default-key" not in response.text
    assert "test-bigmodel-key" not in response.text


@pytest.mark.asyncio
async def test_invalid_selection_is_rejected_before_stream(monkeypatch) -> None:
    settings = _settings()
    settings.aitechflux_api_key = ""
    monkeypatch.setattr("app.api.routes.chat.get_settings", lambda: settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for model_id in ("unknown", "aitechflux-grok-4.7"):
            response = await client.post(
                "/api/v1/agent/chat/stream",
                json={"message": "你好", "model_id": model_id},
            )
            assert response.status_code == 400


@pytest.mark.asyncio
async def test_selected_model_reaches_agent_and_done_event(monkeypatch) -> None:
    monkeypatch.setattr("app.services.chat_service.get_settings", _settings)
    monkeypatch.setattr("app.api.routes.chat.get_settings", _settings)
    captured = []

    class Provider:
        def supports_stream(self):
            return False

        async def chat_with_tools(self, *, messages, tools):
            return {"role": "assistant", "content": "今晚可以吃清淡一些。"}

    def build(settings, model_id=None):
        captured.append(model_id)
        return Provider()

    monkeypatch.setattr("app.services.chat_service.build_llm_provider", build)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/agent/chat",
            json={"message": "你好", "model_id": "aitechflux-grok-4.7"},
        )
    assert response.status_code == 200
    assert captured == ["aitechflux-grok-4.7"]
    assert response.json()["model_id"] == "aitechflux-grok-4.7"
    assert response.json()["model_label"] == "Grok 4.7"
