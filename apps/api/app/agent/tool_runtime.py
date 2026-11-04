"""Strict read/compute/prepare loop with JSON-only state and injected dependencies."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class QueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=500)


class ToolResult(BaseModel):
    status: Literal["ok", "no_result", "partial", "unavailable", "error"]
    data: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    data_versions: dict = Field(default_factory=dict)
    error_code: str | None = None


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    effect: Literal["read", "compute", "prepare"]
    handler: Callable[[BaseModel], Awaitable[ToolResult]]
    timeout: float = 5

    def declaration(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }


async def dispatch(tool: Tool | None, arguments: str) -> ToolResult:
    if tool is None:
        return ToolResult(status="error", error_code="UNKNOWN_TOOL")
    try:
        body = tool.input_model.model_validate_json(arguments)
    except (ValidationError, ValueError):  # Translate untrusted model arguments into a tool result.
        return ToolResult(status="error", error_code="INVALID_ARGUMENTS")
    try:
        async with asyncio.timeout(tool.timeout):
            return await tool.handler(body)
    except TimeoutError:  # Let the agent continue after a tool exceeds its budget.
        return ToolResult(status="unavailable", error_code="TOOL_TIMEOUT")
    except Exception:  # Isolate handler failures as structured tool errors.
        return ToolResult(status="error", error_code="TOOL_FAILED")


async def run_loop(
    provider: Any,
    tools: list[Tool],
    messages: list[dict],
    *,
    fact_version: str,
    max_calls: int = 6,
    max_rounds: int = 7,
) -> dict:
    registry = {tool.name: tool for tool in tools}
    if len(registry) != len(tools):
        raise ValueError("duplicate tool name")
    messages = json.loads(json.dumps(messages))
    cache: dict[str, dict] = {}
    used = 0
    for _ in range(max_rounds):
        async with asyncio.timeout(20):
            response = await provider.chat_with_tools(
                messages=messages, tools=[tool.declaration() for tool in tools]
            )
        calls = response.get("tool_calls", [])
        if not calls:
            return {
                "status": "succeeded",
                "text": response.get("content", ""),
                "messages": messages,
                "tool_calls_used": used,
            }
        messages.append(
            {"role": "assistant", "content": response.get("content"), "tool_calls": calls}
        )
        for call in calls:
            function = call["function"]
            tool = registry.get(function["name"])
            arguments = function["arguments"]
            try:
                normalized = json.dumps(
                    json.loads(arguments), sort_keys=True, separators=(",", ":")
                )
            except (ValueError, TypeError):  # Invalid JSON still reaches dispatch for validation.
                normalized = arguments
            key = json.dumps([function["name"], normalized, fact_version])
            if tool and tool.effect == "read" and key in cache:
                result = cache[key]
            else:
                if used >= max_calls:
                    return {
                        "status": "budget_exhausted",
                        "messages": messages,
                        "tool_calls_used": used,
                    }
                used += 1
                result = (await dispatch(tool, arguments)).model_dump(mode="json")
                if tool and tool.effect == "read" and result["status"] in {"ok", "no_result"}:
                    cache[key] = result
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
    return {"status": "budget_exhausted", "messages": messages, "tool_calls_used": used}


def merge_constraints(previous: dict, explicit: dict, permanent: list[str]) -> dict:
    """Only explicit fields change; immutable server restrictions are always re-applied."""
    allowed = {"diners", "excluded_ingredients", "dish_ids"}
    if set(explicit) - allowed:
        raise ValueError("unknown constraint")
    result = json.loads(json.dumps(previous))
    if "diners" in explicit:
        diners = explicit["diners"]
        if type(diners) is not int or not 1 <= diners <= 30:
            raise ValueError("invalid diners")
    for field in ("excluded_ingredients", "dish_ids"):
        if field in explicit and (
            not isinstance(explicit[field], list)
            or not all(isinstance(value, str) for value in explicit[field])
        ):
            raise ValueError("invalid list constraint")
    result.update(explicit)
    result["excluded_ingredients"] = sorted(
        set(result.get("excluded_ingredients", [])) | set(permanent)
    )
    return result
