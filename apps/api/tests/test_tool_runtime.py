import json

import pytest

from app.agent.tool_runtime import (
    QueryInput,
    Tool,
    ToolResult,
    dispatch,
    merge_constraints,
    run_loop,
)


async def handler(body):
    return ToolResult(status="ok", data={"query": body.query})


def tool():
    return Tool("inventory", "Read confirmed inventory", QueryInput, "read", handler)


@pytest.mark.parametrize(
    "arguments", ["[]", "{", '{"query":1}', '{"query":"egg","workspace_id":"other"}']
)
async def test_invalid_arguments_do_not_execute(arguments):
    result = await dispatch(tool(), arguments)
    assert result.error_code == "INVALID_ARGUMENTS"


async def test_loop_refills_matching_call_and_reuses_read():
    class Scripted:
        count = 0

        async def chat_with_tools(self, *, messages, tools):
            self.count += 1
            if self.count == 3:
                assert messages[-1]["tool_call_id"] == "call-2"
                assert json.loads(messages[-1]["content"])["data"]["query"] == "egg"
                return {"content": "有确认记录"}
            if self.count == 2:
                assert messages[-1]["tool_call_id"] == "call-1"
            return {
                "tool_calls": [
                    {
                        "id": f"call-{self.count}",
                        "type": "function",
                        "function": {"name": "inventory", "arguments": '{"query":"egg"}'},
                    }
                ]
            }

    result = await run_loop(
        Scripted(), [tool()], [{"role": "user", "content": "鸡蛋"}], fact_version="v1"
    )
    assert result["status"] == "succeeded" and result["tool_calls_used"] == 1


async def test_errors_consume_budget():
    class Endless:
        async def chat_with_tools(self, **kwargs):
            return {
                "tool_calls": [{"id": "bad", "function": {"name": "unknown", "arguments": "{}"}}]
            }

    result = await run_loop(Endless(), [], [], fact_version="v1", max_calls=2)
    assert result["status"] == "budget_exhausted" and result["tool_calls_used"] == 2


def test_constraint_merge_keeps_server_allergy_and_previous_dishes():
    previous = {
        "diners": 2,
        "excluded_ingredients": ["shrimp", "coriander"],
        "dish_ids": ["a", "b"],
    }
    result = merge_constraints(previous, {"diners": 3, "excluded_ingredients": []}, ["shrimp"])
    assert result == {"diners": 3, "excluded_ingredients": ["shrimp"], "dish_ids": ["a", "b"]}
    assert previous["diners"] == 2
