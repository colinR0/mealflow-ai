# Repository Guidelines

## 项目结构

`apps/api/app/` 是 FastAPI 后端，`apps/api/tests/` 是 pytest 测试，数据库迁移位于 `apps/api/migrations/`。Vue 3 前端位于 `apps/web/src/`；种子菜谱、开发脚本和项目文档分别位于 `data/seed/`、`scripts/` 和 `docs/`。

## 开发与验证命令

使用 Python 3.12/3.13、Node.js 20+ 和 PostgreSQL。应用运行时只支持 PostgreSQL；SQLite 仅作为旧库只读导入格式。统一演示使用 `docker compose -f docker-compose.s7.yml up --build`，数据库映射 `127.0.0.1:5435`、API `8003`、Web `5176`。pytest 前设置 `MEALFLOW_TEST_DATABASE_URL` 指向独立且数据库名以 `_test` 结尾的 PostgreSQL 数据库，例如 `postgresql+psycopg://mealflow@127.0.0.1:5434/mealflow_s7_test?gssencmode=disable`；测试会重建其中的 ORM 表，禁止指向演示或业务库。在 `apps/web/` 执行 `npm ci`、`npm run typecheck` 和 `npm run build`。

## 代码风格

Python 使用四空格缩进和 `snake_case` 命名；Ruff 配置的行宽为 100 字符。Vue/TypeScript 沿用现有风格：两空格缩进、单引号、不写分号，组件文件使用 PascalCase。业务规则和安全校验放在后端服务中，避免前端重复实现。

## 测试要求

从仓库根目录设置 `$env:MEALFLOW_TEST_DATABASE_URL='postgresql+psycopg://mealflow@127.0.0.1:5434/mealflow_s7_test?gssencmode=disable'`，再执行 `..\.venv\Scripts\python.exe -m pytest apps/api -q`。新增后端测试以 `test_*.py` 命名，覆盖修改过的 Agent 约束、接口行为或数据库迁移。项目目前没有配置最低覆盖率要求。

## 提交与 PR

近期提交同时使用中文描述和 `feat:`、`fix:` 等前缀；提交标题应简短且说明具体改动。一个分支聚焦一项可验收变更。PR 说明目的、修改内容、验证结果和风险；界面改动附截图，接口改动附示例。

## 配置与 Agent 协作

从 `.env.example` 创建本地 `.env`，不得提交密钥、本地数据库或构建产物。开始任务前检查 Git 状态及相关代码；引用文档前确认文件仍存在、内容与当前实现一致。
