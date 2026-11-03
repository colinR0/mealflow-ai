# S6/S7 图与 worker 实现验证（2026-11-03）

实际入口：`app.worker.compute_result` → `app.agent.durable_graph.compute`。
默认 `FakeProvider` 离线发出标准 function calls，经过严格 Pydantic 校验、真实数据库工具、
LangGraph 路由、引用验证和持久化，不再调用 `v2_runtime.request_status`。

## 已实现

- JSON-only StateGraph：load_authorized_context → decide ↔ execute_tool → validate_answer。
- PostgreSQL 使用已安装 `langgraph-checkpoint-postgres` 的 `PostgresSaver`，实际 setup、
  put、put_writes、get_state、invoke 恢复；thread_id 为 run_id。SQLite 兼容路径为内存 Saver，
  不宣称具备跨进程图恢复。
- PG checkpoint 写入、工具预留/完成、模型调用预留均检查当前 worker、attempt、租约、
  run 状态、取消、家庭权限和期限。PG Saver 写入期间持有短期 job/run guard，防旧 attempt
  污染新检查点；外部 provider/tool 执行不持数据库事务。
- tool_executions 先预留 running 再执行，记录输入 hash、状态、结果、耗时；完成记录在图节点
  checkpoint 前提交，节点崩溃后复用。相同事实版本/约束/参数的成功只读结果可跨 call_id 复用。
  未完成只读/计算/prepare 工具可重做，工具不写业务事实。tool_started/tool_finished 为持久事件。
- 工具注册表：家庭限制、库存、偏好摘要、已发布菜谱搜索/详情、可用性检查、菜单、采购预览、
  偏好预览。HTTP action service 保留唯一业务提交责任；图不依据聊天“好的”写入。
- 每轮读取当前家庭全部 profile 永久限制并合并旧会话、本轮显式人数与忌口；清除本轮忌口仍
  重新应用永久限制。换第二道菜保留其他菜和已合并约束，失效菜单请求重新规划。
- 默认最多 6 次实际工具调用、7 次模型请求；调用前持久预留，失败/崩溃不退还预算。
- 模型请求 20 秒、工具各自超时、run 从 created_at 起总计 60 秒；worker 超时能结束阻塞计算，
  晚到工具或 checkpoint 被 guard 拒绝。总期限含排队/重试，不含后续新 run。
- 恢复、执行工具和最终校验读取事实版本，限制/库存/来源审批/菜谱发布版本变化拒绝旧结果。
- citations 与正文 `[recipe:...]` / `[lot:...]` 仅接受本图实际工具提供的证据 ID。

## 验证

最终全量后端：`python -m pytest apps/api -q`，**161 passed in 45.07s**。
本次修改的 Python 文件 Ruff 检查：**All checks passed**。

`MEALFLOW_TEST_POSTGRES_URL=postgresql+psycopg://mealflow@localhost:5433/mealflow_test`，
复用显式启用、随机临时 schema fixture；不清理业务 schema。

`test_durable_graph.py` 覆盖真实图与工具/动作提交边界、多轮约束及局部换菜、未知引用拒绝、
总期限、模型失败预算、跨 call_id 只读复用、事实变更、晚到工具取消，以及 PG 图节点崩溃后
新 worker 恢复、旧 attempt fencing、工具不重复执行。`test_worker_leases.py` 保留原租约故障
语义，将故障注入入口从移除的规则函数迁到 compute_result。

## 尚未宣称完成

- 当前菜单确定性挑选已批准、已发布、有食材及步骤的菜谱，支持局部替换；未实现正式角色分桶、
  可靠份量缩放/单位换算/缺口数量、结构化设备/时长/预算偏好和完整食材过敏原本体。
  可用性工具返回 quantity_sufficient=null 并要求核对，不能声称精确足量或医学安全。
- search_recipes 为已批准发布文档的小规模词项排序；正式 S5 hybrid/index worker 由其他任务负责。
- 自然语言约束提取当前为有限中文规则；复杂否定、目标成员选择及显式菜品锁定需要更完整契约。
- PG 图检查点和业务动作分属不同事务，图只预览，动作幂等由现有 HTTP action service 保证。
  waiting_confirmation 释放 worker；当前不做 HTTP 决定后再恢复已结束图的通知节点。
- 默认 worker 使用 FakeProvider；Runtime 支持注入实际 provider，但未调用付费模型或宣称模型效果。
- 未修改 v2_runtime.py、迁移、鉴权/配额实现、前端或其他 agent 的业务工作。
