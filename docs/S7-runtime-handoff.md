# S7 本地运行链路交接（2026-11-03）

当前是可运行的 v2 demo 基础链路，**未完成 S0–S7 全部验收**。旧 v1 行为保留。
没有调用付费模型、发送客户数据或提交 Git。已有 `.idea/dataSources.xml`、
`InventoryView.vue` 和 `output/` 用户工作未改动。

## 当前可用

- v2 Cookie 会话、Argon2 密码验证、Origin/CSRF 校验、退出撤销、登录失败限流。
- 家庭列表/创建、owner 邀请生成、档案创建/列表；服务层支持一次性接受邀请，尚无 HTTP 接受入口。
- 私有会话创建 run 返回 202；创建任务和 queued 事件在同一事务提交。
- 同会话活动 run 冲突返回 409；创建 run 支持幂等键。
- 独立 worker 使用数据库任务、60 秒租约、PG SKIP LOCKED 领取语句、过期重领和有限重试。
- worker 本地规则响应、确认库存记录查询、购物清单待确认动作；未知量保留为空。
- 确认事务检查动作归属、家庭成员、版本、hash、期限；写入购物项或库存/流水/审计。
- action 重复同决定返回原结果；相反决定冲突；偏好修改检查档案版本。
- 持久化有序事件、SSE Last-Event-ID 重放、run 游标隔离、事件历史过期 410、最终结果恢复。
- `/admin/durable-agent` Options API 联调页：登录、家庭选择、提交、取消、恢复、确认/拒绝。
- 独立严格工具循环模块：参数校验、匹配 call_id 回填、只读结果复用、预算、工具超时。
- 独立 demo 检索模块：仅已批准/发布/当前版本/活动 generation 可见，父菜谱 RRF、证据映射；fake embedding 明示。
- 新增 20 张 v2 表的固定 Alembic 迁移；Docker 镜像包含迁移材料。

## 本地启动（PowerShell）

以下命令从 `apps/api` 执行。为联调使用独立数据库，不要覆盖原 SQLite 文件。
先安装项目依赖，设置同一个 `DATABASE_URL`，再在每个终端运行相应命令。

```powershell
..\..\.venv\Scripts\python.exe -m pip install -e ".[dev]"
$env:DATABASE_URL = 'sqlite:///D:/Agent/mealflow-ai/data/local/s7-demo.sqlite3'
..\..\.venv\Scripts\python.exe -m alembic upgrade head
..\..\.venv\Scripts\python.exe -m app.create_local_account local@example.test
```

账号命令交互读取至少 12 字符的密码，不记录明文、不修改已存在账号。

API 终端：

```powershell
$env:DATABASE_URL = 'sqlite:///D:/Agent/mealflow-ai/data/local/s7-demo.sqlite3'
..\..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

worker 终端：

```powershell
$env:DATABASE_URL = 'sqlite:///D:/Agent/mealflow-ai/data/local/s7-demo.sqlite3'
..\..\.venv\Scripts\python.exe -m app.worker
```

前端在 `apps/web` 执行 `npm run dev`，访问
`http://localhost:5173/admin/durable-agent`，使用刚创建的账号登录。
输入“买点鸡蛋”，应先显示待确认卡片，点击确认才创建采购项。
断开 SSE 后可点“恢复结果”，不会重新创建 run。
SQLite 只支持本地单 worker 联调；不要用它验证多个 worker 的锁竞争。

PG 本地 Compose 模板位于仓库根 `docker-compose.s7.yml`：

```powershell
$env:MEALFLOW_LOCAL_DB_PASSWORD = '<设置本地测试密码，URL 特殊字符需要编码>'
docker compose -f docker-compose.s7.yml up --build
docker compose -f docker-compose.s7.yml exec api python -m app.create_local_account local@example.test
```

该模板使用 PostgreSQL 17.6，尚未在此环境运行；它不包含 pgvector 扩展。
目前未收到可用的外部 PostgreSQL 测试连接，不能声称 PG 通过。

## 已执行验证

|命令/范围|结果|
|---|---|
|根目录 `.\.venv\Scripts\python.exe -m pytest apps/api -q`|118 passed in 35.28s（原 103 项保留）|
|新增 Python 模块及测试的 Ruff 检查|All checks passed|
|`apps/web`: `npm run typecheck`|通过|
|`apps/web`: `npm run build`|通过；主 JS 约 1.21MB，有 chunk 大小提示|
|临时独立 SQLite 文件完整 Alembic `upgrade head`|通过，head `07a1b2c3d4e5`|

测试覆盖：工具非法参数不执行、tool_call_id 回填、只读复用、错误计预算、永久限制保留、
run 私有权限/家庭越权/忙碌、同键创建复用、动作版本冲突、未知量校验、重复确认、取消、
租约过期重领及旧 worker 结果丢弃、SSE 重放及外 run 游标拒绝、Cookie/CSRF/退出/限流、
知识撤销和版本不匹配不可见。
这些测试使用 SQLite，不能替代 PostgreSQL 锁和多进程实测。

## 未完成的目标和风险

1. S4：旧库存、限制和档案未迁入 v2；v1/v2 是独立事实边界。尚无迁移对账与真实 PG 测试。
2. S5：没有原生 pgvector、预分词全文 SQL、索引 worker、generation 重建切换或 80 条冻结评测。
   demo 检索是小规模 Python 扫描，不应称正式 RAG；未自动发布 937 条未审核数据。
3. S6：工具循环和约束合并仅独立测试，尚未接入持久化 worker；没有新 LangGraph 图或 PG Saver。
   已声明并安装 checkpoint 依赖不等于已经使用。模型引用校验和完整配餐多轮尚缺。
4. S7：本地规则计算很短，没有执行期 15 秒心跳；不能直接扩展为长期网络任务。
   工具结果表尚未用于崩溃复用；运行总超时、并发配额、队列上限、退避故障矩阵待实现。
5. 同时首次提交相同幂等键的 PG 唯一约束竞争尚未处理为稳定重放响应；需要独立连接测试和冲突重读。
6. SSE 每秒轮询并发送注释心跳；订阅时鉴权且每次循环重查 run 权限，但尚未进行浏览器断线/过期全链路实测。
7. 本次前端只新增联调入口，旧 UI 与 v1 未迁移，未修改原 ConversationWorkbench 或用户库存组件。
8. 本地账号创建工具仅开发使用，生产邀请制开户、Cookie 轮换、统一错误格式和完整分页待完成。

下一个实现优先级：S4 PG 全链迁移/数据对账/双连接事务测试 → S6 工具图与 PG 检查点接入 →
S7 心跳/取消/恢复故障测试 → S5 正式索引和冻结评测。完成后再做浏览器端联调验证。

## S4 补充落地（2026-11-03）

本段更新以上 S4、邀请及创建准入的旧状态。详见 `docs/S4-migration-admission.md`。

- 新增 `08b2c3d4e5f6`，保留 `07a1b2c3d4e5` 历史结构，SQLite/PG 全链升级实测通过。
- `python -m app.migrate_legacy` 显式只读导入源 SQLite 的库存、档案、限制、偏好、成员事件；
  原表和原文件保留。来源记录及目标 hash 支持幂等重放与 `--reconcile` 对账。
- 不读取/导入旧密码、不创建 legacy 登录账户；档案保留 legacy_user_id，linked_user_id 为空。
  旧格式密码 hash 登录统一返回 401。
- 新增 `POST /api/v2/invites/accept`，沿用 Cookie/Origin/CSRF，并校验一次性及过期。
- create_turn 事务加入固定数据库锁行、每家庭 queued/running 配额、全局 queued job 上限；
  已有 savepoint 幂等预占保留，重放优先于限流，拒绝不残留预占或任务。
- PostgreSQL localhost:5433 / mealflow_test、17.11 实测：随机独立 schema，
  两个实际 backend 连接争抢家庭额度及跨家庭全局队列；各仅一个成功。
- 本次相关测试 29 passed in 20.79s；全量后端 155 passed in 43.77s；相关模块 Ruff 通过。
  验证命令：设置 `MEALFLOW_TEST_POSTGRES_URL` 后执行根目录 pytest apps/api -q。
- 未对原始业务库执行正式迁移；不是全部 legacy 表的通用复制器。共享限制档案、
  legacy_details.events 日期有效期需下游约束加载器接入；不代表完整 S4–S7 产品验收。
  全局模型执行并发仍需 worker 层控制，此处队列准入不替代 worker semaphore。
