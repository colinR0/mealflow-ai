# Repository Instructions

## Boundaries

- `apps/api/` owns FastAPI, PostgreSQL models, Alembic, and pytest; `apps/web/` is the uni-app Android client (Vue 3 + TypeScript), not the removed Vite admin. `data/seed/` contains seed data; `docs/README.md` records current decisions and implementation gaps.
- PostgreSQL is the only supported database. The API enforces Alembic head at startup; before migration or startup, check the configured `DATABASE_URL` and inspect `alembic current` / `alembic heads` from `apps/api/`.

## Commands (PowerShell)

- This Windows workspace uses the Conda `mealflow` interpreter at `D:\APP\miniconda3\envs\mealflow\python.exe`; select it as the IDE project and run-configuration interpreter.
- Install backend/dev dependencies from the repository root: `& 'D:\APP\miniconda3\envs\mealflow\python.exe' -m pip install -e ".\apps\api[dev]"`.
- Apply Alembic migrations from `apps/api/` only after checking the configured `DATABASE_URL` and current/head revisions: `& 'D:\APP\miniconda3\envs\mealflow\python.exe' -m alembic upgrade head`.
- Run API and worker in separate terminals from `apps/api/`: `& 'D:\APP\miniconda3\envs\mealflow\python.exe' -m uvicorn app.main:app --reload --port 8000` and `& 'D:\APP\miniconda3\envs\mealflow\python.exe' -m app.worker`.
- Run backend tests from root only with `MEALFLOW_TEST_DATABASE_URL` set to a disposable PostgreSQL database ending in `_test`; the autouse fixture drops and recreates ORM tables for every test. Never use a development/demo database.
- Backend lint from root: `& 'D:\APP\miniconda3\envs\mealflow\python.exe' -m ruff check apps/api`.
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
