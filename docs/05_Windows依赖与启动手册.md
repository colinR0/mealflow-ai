# MealFlow Windows 依赖与启动手册

适用于在 Windows x64 上独立开发和联调。核对基线为仓库提交 `2178345`，日期为 2026-11-04。以下命令使用 PowerShell。终端命令默认从仓库根目录开始，文中标明了需要切换的目录。

本地启动顺序为 **PostgreSQL → Alembic 迁移 → 测试账号 → FastAPI / Worker → H5 或 Android**。想先看页面，可以优先走 H5。

## 1. 安装清单

| 工具 | 版本与用途 | 获取方式 |
| --- | --- | --- |
| Git | 本机核对为 2.54.0.windows.1，仓库未固定版本 | [Git for Windows](https://gitforwindows.org/) |
| Python | 仓库支持 3.12、3.13。本机虚拟环境为 3.13.14，后端容器用 3.12 | [Python Windows 下载](https://www.python.org/downloads/windows/) |
| Node.js / npm | 按仓库 README 使用 Node.js 22.12 或更高。本机为 22.22.3 / npm 10.9.8 | [Node.js 下载](https://nodejs.org/en/download) |
| PostgreSQL | 本机便携方案为 16.15。仓库 Compose 镜像为 17.6 | 便携版见第 2 节。安装版见 [PostgreSQL Windows 下载](https://www.postgresql.org/download/windows/) |
| pgvector | 本机便携方案为 0.8.6，用于原生向量检索。基础启动使用默认 `RETRIEVAL_MODE=demo` | 第 2 节随便携数据库一起安装 |
| HBuilderX | 5.34 或更高。用于 Android 导入、调试和打包 | [HBuilderX 下载](https://www.dcloud.io/hbuilderx.html) |
| Docker Desktop + Compose v2 | 可选。用于容器化启动数据库、API 和 Worker | [Docker Desktop Windows 安装](https://docs.docker.com/desktop/setup/install/windows-install/) |

用 `git --version`、`py -0p`、`node -v`、`npm.cmd -v` 核对本机环境。数据库命令行客户端 `psql` 已随便携 PostgreSQL 提供。需要图形界面时可以另装 DBeaver 或 pgAdmin。

**应用依赖由项目统一安装。** 后端以 `apps/api/pyproject.toml` 为准。FastAPI ≥0.115 且 <1、SQLAlchemy ≥2.0 且 <3、Alembic ≥1.14 且 <2、LangGraph ≥0.4 且 <2。固定依赖包括 psycopg 3.2.9、langgraph-checkpoint-postgres 3.0.4、argon2-cffi 25.1.0、jieba 0.42.1。`[dev]` 会安装 pytest、Ruff 等开发依赖。

前端用 `npm ci` 安装 `apps/web/package-lock.json` 锁定版本。当前为 uni-app / Vite 插件 `3.0.0-5030420261029002`、Vue 3.5.44、Vue Router 4.6.4、Vite 5.2.8、TypeScript 5.7.3、Sass 1.107.1 和 vue-tsc 2.2.12。

## 2. 准备便携版 PostgreSQL

本机现有实例使用 conda-forge 的 Windows 二进制包。下面在各自电脑上创建一套独立实例，程序和数据放在 `%LOCALAPPDATA%\MealFlow\postgres16`，由 `pg_ctl` 启停。仓库不包含数据库程序或现成数据。

### 下载程序

按 [Micromamba Windows 安装说明](https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html#windows) 取得 `micromamba.exe`。以下假设可执行文件放在 `%LOCALAPPDATA%\MealFlow\tools\micromamba.exe`。

```powershell
$mealflowLocal = Join-Path $env:LOCALAPPDATA 'MealFlow'
$mealflowMamba = Join-Path $mealflowLocal 'tools\micromamba.exe'
$mealflowPg = Join-Path $mealflowLocal 'postgres16'
$env:MAMBA_ROOT_PREFIX = Join-Path $mealflowLocal 'mamba-cache'
& $mealflowMamba create --yes --prefix $mealflowPg --override-channels -c conda-forge 'postgresql=16.15' 'pgvector=0.8.6'

$mealflowPgBin = Join-Path $mealflowPg 'Library\bin'
$mealflowPgData = Join-Path $mealflowPg 'pgdata'
& "$mealflowPgBin\postgres.exe" --version
```

### 首次初始化并建库

在刚才的终端执行。只对新数据目录运行 `initdb`。按提示给本地数据库角色 `mealflow` 设置密码。提前检查 `5434`，已有实例占用时应先确认实例归属，或选用其他端口并同步修改下面的命令和 `DATABASE_URL`。

```powershell
Get-NetTCPConnection -State Listen -LocalPort 5434 -ErrorAction SilentlyContinue
& "$mealflowPgBin\initdb.exe" -D $mealflowPgData -U mealflow -A scram-sha-256 -W -E UTF8 --no-locale
& "$mealflowPgBin\pg_ctl.exe" -D $mealflowPgData -l "$mealflowPg\postgres.log" -o '-h 127.0.0.1 -p 5434' -w start
& "$mealflowPgBin\createdb.exe" -h 127.0.0.1 -p 5434 -U mealflow -W mealflow_s7
& "$mealflowPgBin\psql.exe" -X -h 127.0.0.1 -p 5434 -U mealflow -W -d mealflow_s7 -c 'SELECT current_database(), version();'
```

日常只需重跑 `pg_ctl ... start` 一行。关机前可用 `& "$mealflowPgBin\pg_ctl.exe" -D $mealflowPgData -m fast -w stop` 停止数据库。数据库客户端连接参数为 `127.0.0.1`、端口 `5434`、库名 `mealflow_s7`、用户名 `mealflow`。

## 3. 安装依赖并启动后端

在仓库根目录创建项目内的虚拟环境。只装了 Python 3.12 时，把 `-3.13` 改成 `-3.12`。

```powershell
py -3.13 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e ".\apps\api[dev]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

编辑仓库根目录的 `.env`。把下面的 `YOUR_DB_PASSWORD` 换成本机数据库密码，密码含 `@`、`#` 或 `%` 等字符时使用 URL 编码。以下设置配合本手册的 H5 端口。已有的 PowerShell 环境变量或 `apps/api/.env` 也可能影响实际连接，迁移前核对目标。

```dotenv
APP_ENV=development
DATABASE_URL=postgresql+psycopg://mealflow:YOUR_DB_PASSWORD@127.0.0.1:5434/mealflow_s7?gssencmode=disable
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
RETRIEVAL_MODE=demo
```

模型密钥可在页面和基础接口联调阶段留空。要验证真实模型对话和工具调用，再按 `.env.example` 设置实际提供商、模型、地址和密钥。当前 Worker 在模型未配置时会报告 `MODEL_NOT_CONFIGURED`。项目配置和密钥只留在本机。

从仓库根目录进入 `apps/api`，先核对连接目标与迁移版本，再升级和创建账号。新库执行 `alembic current` 时通常还没有版本记录。测试手机号可替换，密码按终端提示输入。项目会同时创建该账号的联调家庭。

```powershell
Set-Location apps/api
& ..\..\.venv\Scripts\python.exe -m alembic current
& ..\..\.venv\Scripts\python.exe -m alembic heads
& ..\..\.venv\Scripts\python.exe -m alembic upgrade head
& ..\..\.venv\Scripts\python.exe -m alembic current
& ..\..\.venv\Scripts\python.exe -m app.create_local_account 13900000000
& ..\..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另开一个终端，从仓库根目录进入 `apps/api`，启动 Agent Worker。它与 API 使用同一个数据库。

```powershell
Set-Location apps/api
& ..\..\.venv\Scripts\python.exe -m app.worker
```

API 正常后，访问 [Swagger](http://localhost:8000/docs) 或运行 `Invoke-RestMethod http://127.0.0.1:8000/api/v1/health`，预期 `status` 为 `ok`。创建账号只会初始化家庭，首页缺少库存数据时可以先验证登录与空态。

## 4. 启动前端

**浏览器 H5。** 另开终端，从仓库根目录执行。打开 [http://localhost:5173/](http://localhost:5173/) 并用上一步创建的账号登录。

```powershell
Set-Location apps/web
npm.cmd ci
npm.cmd run typecheck
npm.cmd run dev:h5
```

开发服务器默认把 `/api` 代理到 `127.0.0.1:8000`。后端用其他端口时，在运行 `dev:h5` 前设置 `$env:MEALFLOW_API_PROXY`，值为后端地址，例如 `http://127.0.0.1:8003`。实际 H5 地址以终端输出为准，端口变化时把实际网页来源加入 `CORS_ORIGINS` 后重启 API。

**Android。** 执行过 `npm ci` 后，在 HBuilderX 导入 `apps/web`，通过「运行」菜单选择 Android 模拟器或真机。也可以在 `apps/web` 执行 `npm.cmd run build:app-android`，产物在 `dist/build/app`。签名 APK 另需 HBuilderX 打包或 Android SDK 与签名配置。Android 模拟器默认 API 地址为 `http://10.0.2.2:8000/api/v2`。真机使用 `VITE_API_BASE_URL` 指向开发机可达的局域网地址，地址末尾包含 `/api/v2`，并让 API 监听局域网地址、允许 TCP 8000 入站。

## 5. 按需启用向量检索

便携环境安装了扩展文件。要在当前数据库启用扩展并创建原生索引结构，在迁移完成后从 `apps/api` 执行：

```powershell
& ..\..\.venv\Scripts\python.exe scripts/install_s5_native_schema.py
```

在数据库里执行 `SELECT extversion FROM pg_extension WHERE extname = 'vector';`，本手册的便携环境应返回 `0.8.6`。要实际使用 `RETRIEVAL_MODE=native`，还需导入并发布菜谱、生成向量、构建活动索引版本。详见 [S5 原生检索](./S5-native-retrieval.md) 和 [S7 向量演示](./S7-local-vector-demo.md)。历史 S7 启动脚本的 Web 模式仍调用旧的 `npm run dev`，启动现行客户端使用第 4 节命令。

## 6. Docker 启动方式

也可从仓库根目录使用 Docker Compose 自建独立演示库。先为当前终端设置 `MEALFLOW_LOCAL_DB_PASSWORD`，供数据库和容器内连接串共同使用。选用随机的 URL 安全字符密码。Compose 会自动迁移，数据库映射 `127.0.0.1:5435`，API 映射 `127.0.0.1:8003`，数据保存在 Docker 命名卷中。

```powershell
docker compose -f docker-compose.s7.yml up --build -d
docker compose -f docker-compose.s7.yml ps
docker compose -f docker-compose.s7.yml exec api python -m app.create_local_account 13900000000
Invoke-RestMethod http://127.0.0.1:8003/api/v1/health
```

该 Compose 方案使用普通 PostgreSQL 镜像，适合基础联调。H5 需另开终端启动，把 `MEALFLOW_API_PROXY` 设为 `http://127.0.0.1:8003`，并以 `npm.cmd run dev:h5 -- --port 5176 --strictPort` 启动，使网页地址与 Compose 中允许的来源一致。

## 7. 联调排查

| 现象 | 先检查 |
| --- | --- |
| 数据库拒绝连接 | `pg_ctl` 是否已启动，端口、库名、密码是否与 `.env` 一致。 |
| API 提示数据库版本不一致 | 在 `apps/api` 对目标库执行 `alembic current` 和 `alembic heads`，确认后执行 `upgrade head`。 |
| H5 能打开，登录或数据加载失败 | 先看 API 健康接口，再看 `MEALFLOW_API_PROXY`、网页端口和 API 的 `CORS_ORIGINS`。 |
| Agent 请求一直排队 | 确认 Worker 已启动且连接到 API 使用的数据库。 |
| HBuilderX 提示缺少编译器 | 在 `apps/web` 执行 `npm ci`，确认 `node_modules/@dcloudio/vite-plugin-uni` 已安装。 |
| 同事只想用已有服务联调 | 使用已有 API 地址和测试账号即可。要独立运行自己的 API 和数据库，则按本文搭建。 |

运行后端 pytest 时，另建一个库名以 `_test` 结尾的 PostgreSQL 数据库，并将 `MEALFLOW_TEST_DATABASE_URL` 指向它。测试会重建表，开发库和演示库应与测试库分开。

配置依据见 [项目 README](../README.md)、[后端依赖声明](../apps/api/pyproject.toml)、[前端依赖锁文件](../apps/web/package-lock.json)、[H5 代理](../apps/web/vite.config.ts) 和 [Compose 配置](../docker-compose.s7.yml)。
