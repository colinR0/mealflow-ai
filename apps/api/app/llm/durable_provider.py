"""Reuse the chat model configuration across the worker's short-lived event loops."""

from openai import OpenAIError

from app.core.config import get_settings
from app.llm.factory import build_llm_provider
from app.llm.model_catalog import resolve_model_profile


class ConfiguredProvider:
    @staticmethod
    def ready() -> bool:
        """Whether a model profile is configured, without building a client."""
        return resolve_model_profile(get_settings(), None) is not None

    async def chat(self, *, messages):
        provider = build_llm_provider(get_settings())
        if provider is None:
            raise RuntimeError("MODEL_NOT_CONFIGURED")
        try:
            return await provider.chat(messages=messages)
        except OpenAIError as exc:
            raise RuntimeError("MODEL_REQUEST_FAILED") from exc
        finally:
            # Each graph decision uses asyncio.run; close connections on their owning loop.
            await provider.client.close()

    async def chat_with_tools(self, *, messages, tools):
        provider = build_llm_provider(get_settings())
        if provider is None:
            return {
                "content": "尚未配置对话模型，请检查服务端默认模型配置。",
                "error_code": "MODEL_NOT_CONFIGURED",
            }
        try:
            return await provider.chat_with_tools(messages=messages, tools=tools)
        except OpenAIError:
            # Translate provider failures into a visible terminal result, without fake replies.
            return {
                "content": "模型服务暂时不可用，请检查模型配置或稍后重试。",
                "error_code": "MODEL_REQUEST_FAILED",
            }
        finally:
            # Each graph decision uses asyncio.run; close connections on their owning loop.
            await provider.client.close()
