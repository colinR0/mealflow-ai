# S4 显式迁移与准入（2026-11-03）

新增 revision `08b2c3d4e5f6` 接在 `07a1b2c3d4e5` 后；原 revision 和原表保持。
新增 `legacy_imports` 来源/快照/hash 对账表、`runtime_admission` 准入锁行，
以及档案 `legacy_details`，用于保留性别、居住状态、备注及有日期的成员事件。

## 操作

先停止旧系统写入，按运维流程备份原 SQLite 并验证备份。迁移工具只读打开指定文件，
不修改文件，不运行源库 schema 初始化。目标数据库先执行完整 Alembic 升级。
正式迁移前，通过受控开户流程在目标库建立独立 owner 账号与家庭。
开发环境可用现有 `python -m app.create_local_account` 交互创建账号。

从 `apps/api` 执行，`DATABASE_URL` 指向目标库，以下 ID 必须替换为实际值：

```powershell
..\..\.venv\Scripts\python.exe -m alembic upgrade head
..\..\.venv\Scripts\python.exe -m app.migrate_legacy --sqlite D:/backups/original.sqlite3 --source backup-20261103-householdA --source-household old-household-id --target-household new-household-id --actor new-owner-user-id
..\..\.venv\Scripts\python.exe -m app.migrate_legacy --sqlite D:/backups/original.sqlite3 --source backup-20261103-householdA --source-household old-household-id --target-household new-household-id --actor new-owner-user-id --reconcile
```

`source` 是这一源家庭备份的稳定标识，每次重放及对账必须相同；其他源家庭使用不同标识。
工具检查 actor 是目标家庭 active owner；这是受控运维 CLI，并非用户身份验证 HTTP 接口。
不得将 CLI 的 `--actor` 当作公开 API 的身份凭证。

输出为 JSON：各源表数量、created、matched 和逐项 issues。无问题退出 0，有问题退出 2。
`--reconcile` 不写入业务数据。导入只追加缺失项，重复执行不会覆盖现有 v2 修改；
来源改变、来源消失、目标缺失或漂移会显式列出。有效记录可先导入，issues 必须人工处理后再切换。
执行中异常会回滚整笔目标事务；不会回滚或修改源文件。

## 数据边界

- 旧 users 只读档案字段，**不读取密码、不导入账号、不自动关联登录用户**。
  所有迁入档案 `linked_user_id=NULL`，保留 `legacy_user_id`。
  v2 登录只验证 Argon2，非法旧 hash 返回 401。
- 每个旧库存项形成独立 `source_type=legacy` 批次及一笔迁移流水，保留 Decimal 数量、
  单位、到期日、旧更新时间，不自动合并批次、不伪造设备归属。
- 个人限制及忌口进入对应档案。全家限制复制到每个迁入档案，并单独保留“家庭共享限制”
  未关联档案，避免空家庭丢失限制。下游约束加载器应将共享档案作为家庭范围限制处理。
- 长期偏好进入档案 preferences；成员事件保留在 legacy_details.events，含原起止日期，
  不将已过期事件转成永久限制。未知成员、缺失食材和非法数量明确报告。
- `legacy_imports` 每个目标保留来源表、原 ID、源快照、源 hash、目标 hash，
  可追踪档案映射和数量/限制变化；不会删除旧 SQLite、旧表或问题记录。
- 该命令覆盖库存、档案、限制、偏好及成员事件；不迁移旧菜谱、设备、聊天、历史菜单或采购。
  没有替代原系统全部数据的通用 SQLite → PG 复制功能。

## HTTP 与准入

`POST /api/v2/invites/accept` 请求 `{"token":"..."}`，需要登录 Cookie、允许的 Origin、
`X-CSRF-Token`。返回 `{"household_id":"..."}`；token 过期或已用返回 410。
接受事务锁定邀请和家庭，防止不同邀请并发创建相同 membership。

`RUNTIME_HOUSEHOLD_ACTIVE_LIMIT=2` 限制每家庭 queued/running 总数，达到上限返回
429 `HOUSEHOLD_RUN_LIMIT`。`RUNTIME_QUEUE_CAPACITY=100` 限制全局 agent_turn queued job 数，
达到上限返回 503 `QUEUE_FULL`。两项配置均须正整数，各 API 实例必须配置一致。
run 创建事务先处理幂等重放、私有会话与 RUN_BUSY，再 UPDATE 固定准入锁行，检查并写入
run/job/event，持锁直到提交；在 PG READ COMMITTED 下跨进程、跨家庭串行准入。
同键重放优先于限流，拒绝后幂等预占也回滚；waiting_input/confirmation 不占家庭活动配额。
队列容量指等待 job，包括退避待重试；这不是全局模型执行并发 semaphore。
worker 的全局模型并发、执行超时、租约与恢复由运行时 worker 层单独负责。

生产服务必须使用 Alembic 初始化锁行；ORM create_all 的 after_create seed 仅用于隔离测试。
锁行缺失返回 503 `ADMISSION_NOT_INITIALIZED`。SQLite 支持本地事务验证，正式并发证据使用 PG。
