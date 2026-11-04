from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from app.llm.protocol_guard import normalize_model_message
from app.llm.provider import LLMProvider


class OpenAICompatibleCloudProvider(LLMProvider):
    def __init__(
        self,
        *,
        api_key: str,
        api_style: str,
        base_url: str,
        model: str,
        timeout_seconds: int,
    ) -> None:
        self.model = model
        self.api_style = api_style
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout_seconds)

    def supports_stream(self) -> bool:
        return self.api_style != "responses"

    async def chat(self, *, messages: list[dict[str, Any]]) -> str:
        if self.api_style == "responses":
            response = await self.client.responses.create(
                model=self.model,
                input=messages,  # type: ignore[arg-type]
            )
            return response.output_text

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            temperature=0.1,
        )
        content = response.choices[0].message.content
        return content or ""

    async def chat_with_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if self.api_style == "responses":
            response_tools = []
            for tool in tools:
                function = tool.get("function", {})
                response_tools.append(
                    {
                        "type": "function",
                        "name": function.get("name", ""),
                        "description": function.get("description", ""),
                        "parameters": function.get("parameters", {}),
                    }
                )
            response = await self.client.responses.create(
                model=self.model,
                input=messages,  # type: ignore[arg-type]
                tools=response_tools,  # type: ignore[arg-type]
                tool_choice="auto",
            )
            return response.model_dump(exclude_none=True)

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            tools=tools,  # type: ignore[arg-type]
            tool_choice="auto",
            # 对话 Agent 每轮只观察一次工具结果，避免模型并行发起一串无依赖查询。
            parallel_tool_calls=False,
            temperature=0.1,
        )
        message = response.choices[0].message
        allowed = {
            str(tool.get("function", {}).get("name"))
            for tool in tools
            if isinstance(tool, dict) and isinstance(tool.get("function"), dict)
        }
        return normalize_model_message(message.model_dump(exclude_none=True), allowed)

    async def healthcheck(self) -> bool:
        try:
            await self.client.models.list()
        except Exception:  # Report provider connectivity failure through the healthcheck contract.
            return False
        return True

    async def chat_stream(
        self, *, messages: list[dict[str, Any]]
    ) -> AsyncIterator[tuple[str, str]]:
        """流式补全:thinking=思维链增量,text=正文增量。chat_completions 与 responses 两种风格均支持。"""
        if self.api_style == "responses":
            stream = await self.client.responses.create(
                model=self.model,
                input=messages,  # type: ignore[arg-type]
                stream=True,
            )
            async for event in stream:
                event_type = getattr(event, "type", "")
                if "reasoning" in event_type:
                    delta = getattr(event, "delta", None)
                    if delta:
                        yield "thinking", delta
                elif event_type == "response.output_text.delta":
                    delta = getattr(event, "delta", None)
                    if delta:
                        yield "text", delta
            return

        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            temperature=0.1,
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                yield "thinking", reasoning
            if delta.content:
                yield "text", delta.content
