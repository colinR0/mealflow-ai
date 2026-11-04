# S7 PostgreSQL 运行交接

应用、worker、检查点与集成测试均使用 PostgreSQL。v1 接口与 v2 持久化 Agent 并存。
统一演示参考 `docs/S7-local-vector-demo.md`，运行：

```powershell
docker compose -f docker-compose.s7.yml up --build
```

本地开发从 `apps/api` 执行，API 与 worker 使用同一个数据库：

```powershell
$env:DATABASE_URL = 'postgresql+psycopg://mealflow@127.0.0.1:5434/mealflow_s7?gssencmode=disable'
..\..\.venv\Scripts\python.exe -m alembic upgrade head
..\..\.venv\Scripts\python.exe -m app.create_local_account 13900000000
..\..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

另开终端，从 `apps/api` 执行 `..\..\.venv\Scripts\python.exe -m app.worker`。
前端在 `apps/web` 执行 `npm run dev`，访问 `http://localhost:5173/admin/durable-agent`。
本地账号命令交互读取密码，不修改已存在账号。

已有 Cookie/CSRF 验证、家庭与邀请、事务幂等、任务准入、租约与恢复、动作确认、
持久化 SSE、LangGraph PostgreSQL 检查点和原生检索演示链路。
实现细节参考 `docs/S4-migration-admission.md`、`docs/S6-S7-graph-verification.md`。

测试使用独立且库名以 `_test` 结尾的 PostgreSQL 数据库，设置
`MEALFLOW_TEST_DATABASE_URL`；并发和迁移测试同时设置 `MEALFLOW_TEST_POSTGRES_URL`。
测试会重建测试表，禁止指向演示或业务库。历史测试数字不作为当前回归结果。

仍需验收浏览器 SSE 断线恢复、真实 embedding 语义质量、完整份量/单位换算与过敏原本体。
fake embedding 只验证集成线路，不能代表语义检索质量；不代表 S0–S7 全部产品验收完成。
