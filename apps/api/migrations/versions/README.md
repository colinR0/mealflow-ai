# Database migrations

表结构由 `app/db/models.py` 和 `app/db/v2_models.py` 定义，本目录保存变更历史。两人通过执行迁移同步 PostgreSQL 表结构。

## 日常命令（在 `apps/api` 目录下执行）

```bash
# 拉取代码后：把本地库升到最新结构
.venv/Scripts/python -m alembic upgrade head

# 修改 models.py 后：生成增量迁移（生成后先人工检查再提交）
.venv/Scripts/python -m alembic revision --autogenerate -m "描述这次改动"

# 查看本地库当前版本 / 检查模型与迁移是否漂移
.venv/Scripts/python -m alembic current
.venv/Scripts/python -m alembic check
```

## 约定

1. 改了 `models.py` 必须在同一 commit 里附上对应的迁移文件。
2. 已推送到远程的迁移文件不再修改，需要变更就追加新迁移。
3. 两人并行改表产生双头时，后推的人把自己的 `down_revision` 改为指向对方最新迁移，`upgrade head` 验证后再推。
4. `create_all` 仅保留给测试使用（`tests/conftest.py`），运行中的应用一律走迁移。
5. 破坏性演示重置只能针对明确指定的独立 PostgreSQL 演示库，业务库升级使用增量迁移。
