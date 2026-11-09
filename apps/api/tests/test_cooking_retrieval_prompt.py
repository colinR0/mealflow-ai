"""The cooking constraints are loaded from the prompt file and reach the model."""

from app.agent import durable_graph as graph
from app.llm import protocol_guard


def test_cooking_constraints_are_loaded_and_wired():
    prompt = graph.build_system_prompt({"constraints": {"diners": 2}})

    assert "烹饪检索与投料约束" in prompt
    assert "你只能使用本次检索返回的菜谱、食材、调味比例和烹饪技法进行组合。" in prompt
    assert "缺少标准配方，只能提供思路，无法给出精确投料分量。" in prompt
    assert "禁止编造不在食材字典里的调料，禁止编造不知名的传统配方。" in prompt
    assert "当前没有经过人工审核的配方版本，不能执行精确克重或自动下料。" in prompt
    assert "食材替换" in prompt
    assert "固定比例只读 `components`、`ratio_basis` 和 `review_status`。" in prompt
    assert "没有可用的已审核风险规则" in prompt
    assert "向量相似度" in prompt
    assert '"diners": 2' in prompt
    assert prompt.index("烹饪检索与投料约束") < prompt.index('"diners": 2')


def test_structured_tools_are_registered_in_the_protocol_whitelist():
    required = {"find_ingredient_substitute", "get_flavor_template", "check_food_risk"}
    assert required <= protocol_guard.DEFAULT_TOOLS
    for name in required:
        assert protocol_guard._REQUIRED_TEXT[name] == "query"