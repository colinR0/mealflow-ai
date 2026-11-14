# 旧 Agent 退役与六个助手工具

- 日期：2026-11-14
- 范围：删除旧对话决策和旧配餐实现，保留运行基础设施，交付六个可独立调用的工具。
- 记录授权：用户确认的收尾方案要求新增本记录。

## 文件

删除旧决策、意图、配餐图和对应服务、路由、测试，包括 `apps/api/app/agent/graph.py`、`durable_graph.py`、`conversation.py`、`intent.py`、`tool_runtime.py`，以及 `agent_service`、`chat_service`、`meal_plan_service`、`menu_composer`、`planning`、`context_loader`。

新增：

- `apps/api/app/tools/contracts.py`、`assistant.py`
- `apps/api/app/cli/call_assistant_tool.py`
- `apps/api/app/agent/runtime.py`（只保留 worker 仍需要的超时和重建错误码）
- `apps/api/tests/test_assistant_tools.py`
- `docs/助手工具封装与重建说明.md`
- 本记录

助手页和配餐页改为重建提示：`apps/web/src/pages/assistant/index.vue`、`meal-plan.vue`。新建 V2 任务在 `create_turn` 鉴权后返回 `503 / AGENT_REBUILDING`，不入队。没有新数据库迁移，没有改研发语料、向量或发布状态。

未提交、未推送。工作区里原有的演讲稿、待审来源、输出 JSONL、个人文档和 `.idea` 保持原样。

## 交付行为

六个工具为 `search_recipes`、`get_recipe_detail`、`get_inventory_summary`、`get_member_profiles`、`get_procurement_list`、`prepare_procurement_action`。身份和家庭由可信上下文注入。菜谱搜索固定走已配置批次的混合检索；未配置批次时只有菜谱工具不可用。采购准备只创建待确认草稿。

App 助手显示“助手正在重建，暂不可用”，不能发送、续接或生成菜单。库存、人员和采购页面未改业务。worker 领取遗留任务后以 `AGENT_REBUILDING` 结束，不调用模型或工具。

## 验证与结果

开发库 `mealflow_s7`（127.0.0.1:5434）未写入。验收前核对仍为 5 条业务菜谱、741 条研发片段。该实例上的 `mealflow_s7_test` 缺少关系文件，`template0` / `template1` 也不是有效数据目录，因此没有在 5434 上跑会删表的 pytest。

测试改用临时 PostgreSQL 16.15 + pgvector 0.8.6，监听 127.0.0.1:5435，库名 `mealflow_s7_test`。检索使用测试里的编码器替身，没有加载 BGE，没有调用生成模型。

- `test_assistant_tools.py` 及 worker、V2、历史、幂等、并发、准入、检索网关、研发 SQL 测试：79 通过，3 跳过。
- 跳过项随后补跑：原生网关两条在单独的 `mealflow_native_pg_test` 上通过；`test_s4_alembic.py` 全量迁移通过。`test_native_pg_without_vector_returns_unavailable` 仍跳过，因为该临时库安装了 vector，这条用例要求没有 vector。
- `apps/api/tests` 共收集 198 个测试，删除旧模块后没有导入错误。
- 变更 Python 文件 `ruff check` 通过。
- `apps/web`：`npm run typecheck` 通过，`npm run build:app-android` 完成。产物在 `apps/web/dist/build/app`，不是签名 APK。没有浏览器点击验收；助手页已是静态提示，没有发送入口。

临时数据库进程在记录写完后关闭。5434 上的开发库没有被替换。

## 尚未实现

没有新的外层 StateGraph，没有配餐 Workflow，没有模型选工具的评估。没有 BM25、Rerank 或 Ragas。没有面向客户端的通用工具执行 API。
