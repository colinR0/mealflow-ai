# RAG 回答、引用与来源展示

- 日期：2026-11-09。
- 用户要求：继续完善 RAG 链路；同意填写本次开发记录。
- 文件：`apps/api/app/agent/durable_graph.py`、`apps/api/tests/test_recipe_rag_flow.py`、`apps/web/src/pages/assistant/index.vue`、`docs/README.md`。

## 行为

沿用 native + fake 64 维和上一批导入的 443 篇整篇单块菜谱。本次不更换 embedding、不重建业务数据。

Agent 的 search_recipes 工具说明明确使用菜名或食材关键词。系统提示要求先检索，再依据 content.steps 顺序回答，不补造用量或烹饪步骤，正文引用工具返回的完整 recipe 证据 ID。

最终校验把正文中的 recipe/lot 引用与结构化 citations 合并去重，继续拒绝工具没有提供的引用。执行菜谱搜索/详情后，有菜谱证据但回答没有菜谱引用时，以 MISSING_RECIPE_CITATION 拒绝结果。最近一次菜谱读取返回 no_result 时，结果为 waiting_input 并要求更换关键词；返回 unavailable/error 时结果为 failed，保留错误码并提示稍后重试，避免模型补写做法掩盖失败。模型自身错误和未知引用继续由原有校验处理。

移动助手页根据结果中 citations 和 evidence 的交集展示菜谱名称、来源名称，支持复制证据中的原始步骤。历史和终态事件沿用原有 result 传输，无新增数据库字段或接口。

## 验证

- 在普通临时 PostgreSQL `_test` 数据库和独立 native `_test` 数据库运行：`pytest apps/api/tests/test_recipe_rag_flow.py apps/api/tests/test_durable_graph.py apps/api/tests/test_retrieval_gateway.py -q`。
- 结果：35 passed、1 skipped；36 条既有 Alembic 配置弃用警告。未对 mealflow_rag 业务库执行 pytest。
- 新增 4 个 native 集成场景：完整图工具循环读取步骤、合并正文引用并由 worker.finish 持久化；缺少引用拒绝；来源审批撤销后返回无结果；缺失活动索引时返回查询失败。
- 新增测试中的模型为确定性 RecipeProvider，实际执行原生检索、LangGraph、检查点与任务持久化，不调用外部模型。
- 修改的两个 Python 文件 Ruff 检查通过；git diff --check 通过。
- `npm run typecheck` 通过。
- `npm run build:app-android` 通过，输出 dist/build/app（不是签名 APK）；存在既有 Sass API/@import 弃用警告。

## 边界与后续

- 真实 LLM 调用、HTTP 会话和手机来源卡片未实际验收；本次验证的是确定性模型驱动的完整后端任务及前端编译。
- 引用校验保证引用来自本轮工具，不保证模型回答每句话均忠实原文；步骤内容忠实性目前依靠提示与可复制原文核对。
- 模型未调用任何菜谱工具就直接回答的情况，当前没有独立的意图门控强制拦截；系统提示要求先查工具。内置 FakeProvider 仍仅用于既有确定性测试，不代表真实菜谱问答模型。
- fake embedding 仍可能召回无关菜谱。下一阶段应新增真实 embedding provider，统一索引/查询配置，再用带期望菜谱的查询集评估召回与排序。
- 多工具复杂任务中，最近一次菜谱读取的无结果或错误会影响最终状态；当前以保证明确反馈为优先。
