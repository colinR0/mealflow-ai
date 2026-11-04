# S4 PostgreSQL 任务准入

项目只使用 PostgreSQL。账号通过 `python -m app.create_local_account` 创建，
家庭与档案通过应用接口维护。

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
锁行缺失返回 503 `ADMISSION_NOT_INITIALIZED`。正式并发证据使用 PostgreSQL 独立连接测试。
