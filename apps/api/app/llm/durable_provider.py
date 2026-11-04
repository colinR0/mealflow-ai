"""Reuse the chat model configuration across the worker's short-lived event loops."""

from openai import OpenAIError

from app.core.config import get_settings
from app.llm.factory import build_llm_provider


class ConfiguredProvider:
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
