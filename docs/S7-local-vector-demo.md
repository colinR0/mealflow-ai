# PG16 + pgvector 独立本地演示

此演示使用 PG16 `127.0.0.1:5434/mealflow_s7`，与 PG17 `5433/mealflow_dev` 的并发证据分开保存。不会删除或迁移 PG17 数据，不控制 Windows 服务，不停止现有 8000/5173 进程。

便携 PostgreSQL 默认目录为 `$env:LOCALAPPDATA\Temp\opencode\mealflow-pgvector16`，二进制在 `Library\bin`，数据在 `pgdata`。角色默认 `mealflow`。脚本检查版本和实际 data_directory，防止误连其他实例；可用 `-PgRoot`、`-PgUser` 覆盖。需要已有 Python 虚拟环境、前端 node_modules 和 PG16 vector 扩展文件。数据库认证沿用本机 libpq 配置或 PGPASSFILE，不在脚本存储数据库密码。

在仓库根目录显式准备数据库：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-s7-demo.ps1 -Mode Prepare
```

Prepare 仅在便携实例不可用时通过 pg_ctl 启动，创建缺失的 `mealflow_s7`，执行 Alembic upgrade head，然后显式执行 `python -m app.seed_s7_demo`。API 和 worker 启动不会自动 seed。单独运行 seed 时必须设置 `APP_ENV=development`、`DATABASE_URL=postgresql+psycopg://mealflow@127.0.0.1:5434/mealflow_s7?gssencmode=disable`，并从 `apps/api` 执行：

```powershell
..\..\.venv\Scripts\python.exe -m app.seed_s7_demo
```

不提供 `--credential-file` 时仅为新账号交互读取密码。Prepare 为新账号生成随机密码，独占写入仓库外 `$env:LOCALAPPDATA\Temp\opencode\mealflow-s7-vector-login.json`，不向控制台打印密码。已有账号的密码不修改；凭据文件遗失后重新 seed 不会恢复或重置密码。可以在本地编辑器打开该文件用于登录，不要把文件内容贴入文档或终端日志。

数据全部自造：`s7-vector@example.test` 的独立家庭、花生/虾永久限制、番茄/鸡蛋/豆腐/大米各 500g 的库存、5 道菜谱（含用于验证限制的花生炒虾仁）、已批准来源、已发布文档、显式 fake 64 维 profile。无客户数据，也不依赖旧 SQLite。使用稳定 ID，仅补缺失项，不覆盖现有演示库存、限制、菜谱和账号密码。有效活动 generation 重跑复用；缺失原生向量或文档版本块时重新构建。原生 schema 安装和 rebuild_generation 均显式调用，构建传入 idle connection，生成 vector、tsvector、HNSW/GIN 索引与活动 generation。fake embedding 仅验证集成线路，不代表语义检索质量。

分别在三个终端运行（各进程前台运行，用 Ctrl+C 停止）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-s7-demo.ps1 -Mode Api
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-s7-demo.ps1 -Mode Worker
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-s7-demo.ps1 -Mode Web
```

API 是 `python -m uvicorn app.main:app --host 127.0.0.1 --port 8002`；worker 是 `python -m app.worker`，必须独立进程；web 是 `npm run dev -- --host 127.0.0.1 --port 5175 --strictPort`，代理指向 8002。三者均由脚本设置同一 PG16 URL。worker launcher 用命名 mutex 防止同一脚本重复启动；其他方式直接启动的 worker 不在其识别范围内。API/web 已有健康监听时不再拉起新进程；端口健康检查不能单独证明进程所连数据库，应结合 Check 的库检查。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-s7-demo.ps1 -Mode Check
.\.venv\Scripts\python.exe scripts/verify_s7_demo.py --credential-file "$env:LOCALAPPDATA\Temp\opencode\mealflow-s7-vector-login.json"
```

Check 检查 PG16 路径、迁移 head、vector 版本、活动 generation、8002 API health 和 5175 HTML。verify 从凭据文件登录，经真实 HTTP 验证永久限制与四条库存，并投递“查看库存”到独立 worker，等待 succeeded 和四份证据；只输出非敏感结果。每次 verify 新建一次演示对话运行，seed 本身不会创建这些运行。

2026-11-03 实测：从空库迁移到 `08b2c3d4e5f6`，vector `0.8.6`，首次 built、第二次 reused 同一 generation `291dc618-6b11-48c4-af7e-52d91141234b`，原生搜索返回 5 份证据；重跑前后全部 12 类演示表行数及密码哈希一致。真实 HTTP 登录、永久限制、库存验证通过，独立 worker 运行 `330c1d07-4ec4-4e9c-9e52-33e0d659ef4a` 为 succeeded，并返回四份库存证据。API/web health 通过，Ruff 检查通过。用户现有 8000/5173 进程和 PG17 保持运行。
