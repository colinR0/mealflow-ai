# Repository Instructions

## Boundaries

- `apps/api/` owns FastAPI, PostgreSQL models, Alembic, and pytest; `apps/web/` is the uni-app Android client (Vue 3 + TypeScript), not the removed Vite admin. `data/seed/` contains seed data; `docs/README.md` records current decisions and implementation gaps.
- PostgreSQL is the only supported database. The API enforces Alembic head at startup; before migration or startup, check the configured `DATABASE_URL` and inspect `alembic current` / `alembic heads` from `apps/api/`.

## Commands (PowerShell)

- Backend requires Python 3.13 (`apps/api/pyproject.toml` declares `>=3.12,<3.14`). Use any local environment that satisfies that range and has `apps/api[dev]` installed, then point the IDE interpreter and run configuration at the same one. Do not hardcode machine-specific absolute interpreter paths or drive letters in shared docs or configs.
- Install backend/dev dependencies from the repository root: `python -m pip install -e ".\apps\api[dev]"`.
- Apply Alembic migrations from `apps/api/` only after checking the configured `DATABASE_URL` and current/head revisions: `python -m alembic upgrade head`.
- Run API and worker in separate terminals from `apps/api/`: `python -m uvicorn app.main:app --reload --port 8000` and `python -m app.worker`.
- Run backend tests from root only with `MEALFLOW_TEST_DATABASE_URL` set to a disposable PostgreSQL database ending in `_test`; the autouse fixture drops and recreates ORM tables for every test. Never use a development/demo database.
- Backend lint from root: `python -m ruff check apps/api`.
- Local recipe retrieval and evaluation (research index) additionally need `torch` and `transformers`, which are not part of the base `pyproject.toml` dependencies. From `apps/api/` run `python -m app.cli.ask_research_recipes`, `python -m app.cli.search_research_recipes` or `python -m app.cli.embed_research_recipes`; the local model directory is `models/bge-small-zh-v1.5`.
- In `apps/web/`, install from the lockfile with `npm ci`, then use `npm run typecheck` and `npm run build:app-android`. HBuilderX imports `apps/web/`; CLI output is `apps/web/dist/build/app`, not a signed APK. HBuilderX may need reopening after `npm ci` if its uni compiler plugin was missing.
- Android emulator API default is `http://10.0.2.2:8000` in `apps/web/src/shared/config.ts`; physical devices need a reachable LAN API address.
- `docker compose -f docker-compose.s7.yml up --build` is a local integration stack (PostgreSQL, migrations, API, worker), not the uni-app client. It requires `MEALFLOW_LOCAL_DB_PASSWORD`; API is published on `127.0.0.1:8003`.

## Non-obvious Constraints

- Keep frontend and backend code concise and easy to understand. Avoid redundant null/empty checks, repeated validation, and excessive defensive branches; each check must protect a real boundary or provide a meaningful fallback.
- Use `try/catch` (Python `try/except`) only for recovery, error translation, cleanup, or user-facing feedback; never suppress errors or duplicate lower-layer handling. Explain non-obvious exception handling with a concise comment.
- Add concise comments explaining intent, invariants, or tradeoffs in core business logic, important state transitions, and security boundaries; do not merely restate the code.
- Do not bypass the startup schema guard to hide pending migrations. Never run Alembic against a database until its configured `DATABASE_URL` has been confirmed.
- Native authenticated requests use Bearer sessions marked native; browser cookie writes retain CSRF validation. Preserve this distinction when changing auth or CORS.
- Keep unknown inventory quantities distinct from known quantities. Visual observations are not confirmed inventory; device images/vision are not connected.
- Agent shared writes (such as procurement) require explicit confirmation before side effects. Product/technical specs are target contracts; verify actual scope against `docs/README.md` and code.
- Check `git status` before editing and preserve unrelated in-progress changes.
- Development records are optional, not mandatory for every code change or code generation. Before starting each Agent development or modification task, ask `Should this change be recorded in the development records?` (when communicating in Chinese, `本次开发/修改是否需要填写开发记录？`). Do not create a development record unless the user explicitly agrees to do so for this task. When agreed, add one new Markdown record under `docs/开发用例/` in the same change, named `YYYYMMDD-HHMM-brief-change-name.md` so filename order reflects chronological order; use a sequence suffix if timestamps collide. Record the date, scope/files changed, behavior delivered, verification performed and results, and any known gaps. Do not overwrite or rename prior records.
- When a task explicitly uses Agent1 for implementation and Agent2 for testing, follow exactly this sequence: Agent1 implements -> Agent2 tests and documents findings -> Agent1 makes one fix pass from that test report -> stop. Do not start another test/fix cycle; report any remaining findings as unresolved.
- Shared docs record environment contracts (Python version range, dependency install command, service versions), never personal absolute paths or drive letters. Keep machine-specific values in `.env` (gitignored) or IDE configuration; local PostgreSQL/pgvector instance, port and database name details belong in `docs/05_Windows依赖与启动手册.md`.
