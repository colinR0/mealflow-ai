# 清淡菜单过滤与裸数字人数回复修复

- 日期：2026-11-12 11:48
- 范围：`apps/api/app/agent/durable_graph.py`、`apps/api/app/agent/tool_runtime.py`、`apps/api/tests/test_durable_graph.py`
- 结论：菜单请求会先按“清淡”过滤全部已审核候选；没有清淡候选时交由 LLM 补充并标注“模型补充 · 未核实”。上一轮询问用餐人数后，用户单独回复“2”会保存为 `diners=2`，不会重复追问。

## 问题

1. 用户要求“清淡”时，`recommend_menu` 仍返回前三道已审核菜谱，其中可能没有清淡菜；此前只修复了点名菜的 RAG 未命中兜底，菜单路径没有同步。
2. 助手询问“请明确本次用餐人数”后，用户回复“2”，助手再次询问人数。

## 根因

- 会话约束没有保存 `dietary_style`；`explicit_constraints()` 只解析带“人/位”的数量表述，单独“2”不会成为 `diners`。
- `recommend_menu` 直接使用 `candidates[:3]`，没有读取口味，也没有“无匹配候选”的 LLM 兜底信号。
- `validate()` 会把 `recommend_menu` 的所有非 `ok` 结果覆盖成 `waiting_input`，即使外层 LLM 已经准备好模型补充菜单。

## 修改

- `explicit_constraints()`：
  - 支持“清淡”持久化为 `dietary_style=["清淡"]`，并支持“不要清淡/重口”等清除语义。
  - 新增 `expects_diners` 参数；只有在上一轮助手处于 `waiting_input` 且明确询问用餐人数时，才把单独数字或“两”解析为人数。
  - “2个菜”不会被当作人数。
- `Runtime.context`：
  - 从最近一条等待补充的助手回复识别“用餐人数/几个人/几位/多少人”，传入 `expects_diners=True`。
  - 对旧会话兼容：如果当前消息和会话状态都没有口味，从最近的历史用户消息回填一次 `dietary_style`。
- `merge_constraints()`：允许并校验 `dietary_style`，去重排序后保存。
- `Runtime.menu`：
  - 对全部候选执行清淡过滤，而不是只看前三个；有清淡候选时只返回清淡候选。
  - 清淡候选为空时返回 `no_result` 和 `fallback_allowed=true`，不把不清淡的候选当作推荐结果。
- `Runtime.validate`：
  - `fallback_allowed` 时保留外层 LLM 生成的正文，在正文前加“本地菜谱库没有符合清淡的已审核菜单；以下为模型补充 · 未核实。”
  - 普通 `recommend_menu` 失败仍保持原来的 `waiting_input` 行为。
- `BASE_SYSTEM_PROMPT`：增加 `fallback_allowed` 时允许使用通用知识生成菜单、必须标注未核实、不得伪造菜谱 ID 的规则。
- 测试：覆盖裸数字解析、菜单选项数字不误判、无清淡候选、清淡候选过滤、LLM 兜底正文不被覆盖。

## 验证

- `python -m py_compile`：通过。
- `ruff check apps/api/app/agent/durable_graph.py apps/api/app/agent/tool_runtime.py apps/api/tests/test_durable_graph.py`：通过。
- `pytest apps/api/tests/test_durable_graph.py -q`：`39 passed, 7 skipped`。
- 关联回归 `test_recipe_fallback.py + test_recipe_rag_flow.py + test_chat_api.py`：`24 passed, 4 skipped, 1 failed`；唯一失败为既有的 `test_list_sessions_returns_recent` 时区断言（本机返回 `+08:00`，测试期望 `+00:00`），与本次功能无关。
- H5 实测（新对话）：
  - 发送“今晚吃清淡一点”后，助手询问用餐人数。
  - 回复“2”后不再重复询问，返回 2 人份清淡菜单：清炒花菜、清蒸生蚝、桂圆红枣粥。
  - 说明：开发库目前有 47 道判定为清淡的已审核菜谱，因此 H5 走的是本地清淡候选；真实 LLM 兜底由 `TasteProvider` 单测覆盖。

## 已知边界

- 真实 LLM 兜底文案的自然度尚未在“零清淡候选”的开发数据集上人工复核；当前单测确认了正文保留和“模型补充 · 未核实”标注。
- 旧会话如果没有可回填的“清淡/重口”历史，用户仍需重新表达一次口味。

未提交、未推送。