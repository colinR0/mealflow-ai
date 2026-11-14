"""Tool declarations and execution boundary, independent of any agent loop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.services.research_retrieval import RetrievalError


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


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
    timeout: float = 10

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
        body = tool.input_model.model_validate_json(arguments, strict=True)
    except (ValidationError, ValueError):
        return ToolResult(status="error", error_code="INVALID_ARGUMENTS")
    try:
        async with asyncio.timeout(tool.timeout):
            return await tool.handler(body)
    except TimeoutError:
        return ToolResult(status="unavailable", error_code="TOOL_TIMEOUT")
    except HTTPException as exc:
        # Translate authorization/domain failures without leaking another household's data.
        code = (
            exc.detail.get("code", "ACCESS_DENIED")
            if isinstance(exc.detail, dict)
            else "ACCESS_DENIED"
        )
        return ToolResult(status="error", error_code=code)
    except RetrievalError as exc:
        status = "error" if exc.code == "evidence_mismatch" else "unavailable"
        return ToolResult(status=status, error_code=exc.code)
    except SQLAlchemyError:
        # Driver messages may contain SQL and credentials; expose a stable code instead.
        logging.warning("Tool database operation failed: %s", tool.name)
        return ToolResult(status="unavailable", error_code="DATABASE_UNAVAILABLE")
    except Exception:
        # Isolate unexpected handler failures at the public tool boundary.
        logging.exception("Tool handler failed: %s", tool.name)
        return ToolResult(status="error", error_code="TOOL_FAILED")
