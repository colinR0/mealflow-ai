"""统一管理可供聊天工作台选择的模型。密钥只从服务端 settings 读取。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import Settings


@dataclass(frozen=True)
class ModelProfile:
    id: str
    label: str
    provider: str
    model: str
    base_url: str
    api_key: str = field(repr=False)
    api_style: str
    supports_tools: bool = True
    supports_stream: bool = True
    is_default: bool = False
    is_free: bool = False
    use_case: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def public_view(self) -> dict[str, object]:
        reason = None if self.enabled else "未配置 API Key 或模型参数"
        return {
            "id": self.id,
            "label": self.label,
            "provider": self.provider,
            "enabled": self.enabled,
            "supports_tools": self.supports_tools,
            "supports_stream": self.supports_stream,
            "default": self.is_default,
            "is_free": self.is_free,
            "use_case": self.use_case,
            "reason": reason,
        }


def list_model_profiles(settings: Settings) -> list[ModelProfile]:
    return [
        ModelProfile(
            id="bigmodel-glm-4.5-air",
            label="GLM-4.5-Air",
            provider="bigmodel",
            model=settings.bigmodel_text_model,
            base_url=settings.bigmodel_base_url,
            api_key=settings.bigmodel_api_key,
            api_style="chat_completions",
            is_default=False,
            use_case="默认文本 Agent｜菜单规划、结构化意图与工具调用",
        ),
        ModelProfile(
            id="bigmodel-glm-4.6v",
            label="GLM-4.6V",
            provider="bigmodel",
            model=settings.bigmodel_vision_model,
            base_url=settings.bigmodel_base_url,
            api_key=settings.bigmodel_api_key,
            api_style="chat_completions",
            use_case="视觉任务｜冰箱照片、食品标签、菜单截图与文件理解",
        ),
        ModelProfile(
            id="deepseek-v4-pro",
            label="DeepSeek V4 Pro",
            provider=settings.llm_provider,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            api_style=settings.llm_api_style,
            is_default=True,
            use_case="通用文本｜复杂推理与对话备选",
        ),
        ModelProfile(
            id="aitechflux-gpt-6.1-sol",
            label="ChatGPT 6.1 Sol",
            provider="aitechflux",
            model=settings.aitechflux_gpt_model,
            base_url=settings.aitechflux_base_url,
            api_key=settings.aitechflux_api_key,
            api_style="chat_completions",
            use_case="高质量文本 Agent｜复杂菜单规划与工具调用",
        ),
        ModelProfile(
            id="aitechflux-grok-4.7",
            label="Grok 4.7",
            provider="aitechflux",
            model=settings.aitechflux_grok_model,
            base_url=settings.aitechflux_base_url,
            api_key=settings.aitechflux_api_key,
            api_style="chat_completions",
            use_case="通用文本｜对话与工具调用备选",
        ),
    ]


def resolve_model_profile(settings: Settings, model_id: str | None) -> ModelProfile | None:
    profiles = list_model_profiles(settings)
    if model_id is None:
        profile = next((item for item in profiles if item.is_default), None)
    else:
        profile = next((item for item in profiles if item.id == model_id), None)
    if profile is None or not profile.enabled:
        return None
    return profile
