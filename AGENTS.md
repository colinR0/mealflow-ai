# Repository Instructions

## Boundaries

- `apps/api/` owns FastAPI, PostgreSQL models, Alembic, pytest, the persistent Agent, and its worker; `apps/web/` is the uni-app + Vue 3 + TypeScript Android client, not the removed Vite admin.
- `docs/README.md` records current decisions and implemented scope; `docs/01_*`–`03_*` are target contracts, not proof of implementation.
- PostgreSQL is the only supported database. The API refuses to start unless the database revision is one of the Alembic heads (`app/db/schema_guard.py`); never bypass that guard.
- Mobile inventory reads and writes use `inventory_lots` plus `inventory_movements`. Legacy `inventory_items` and its `/api/v1` routes are separate and must not be exposed to the mobile client.

## Commands (PowerShell)

- This Windows machine uses the Conda `mealflow` interpreter: `D:\APP\miniconda3\envs\mealflow\python.exe`. The repository `.venv` is Python 3.14 and lacks API dependencies; do not use it for backend work.
- Install backend/dev dependencies from the repo root: `& 'D:\APP\miniconda3\envs\mealflow\python.exe' -m pip install -e ".\apps\api[dev]"`.
- From `apps/api/`, check `alembic current` and `alembic heads` before `& 'D:\APP\miniconda3\envs\mealflow\python.exe' -m alembic upgrade head`. `alembic.ini` has a URL, but `migrations/env.py` overrides it with `DATABASE_URL`, so inspect the real environment instead of editing that line.
- Run API and worker in separate terminals from `apps/api/`: `& 'D:\APP\miniconda3\envs\mealflow\python.exe' -m uvicorn app.main:app --reload --port 8000` and `& 'D:\APP\miniconda3\envs\mealflow\python.exe' -m app.worker`. API startup does not start the worker.
- Run backend tests from the repo root with `MEALFLOW_TEST_DATABASE_URL` pointing at a disposable PostgreSQL database whose name ends in `_test`; `tests/conftest.py` aborts otherwise and drops/recreates all ORM tables per test. Example: `$env:MEALFLOW_TEST_DATABASE_URL='postgresql+psycopg://mealflow@127.0.0.1:5434/mealflow_s7_test?gssencmode=disable'; & 'D:\APP\miniconda3\envs\mealflow\python.exe' -m pytest apps/api/tests/test_inventory_lifecycle_api.py -q`. Never point it at a development/demo database.
- Backend lint from the repo root: `& 'D:\APP\miniconda3\envs\mealflow\python.exe' -m ruff check apps/api`.
- In `apps/web/`: `npm ci`, `npm run typecheck`, `npm run build:app-android`. CLI output is `apps/web/dist/build/app`, not a signed APK.
- Frontend component tests run directly: `node --test tests/inventory-retry.test.mjs` (in `apps/web`). H5 dev server: `npm run dev:h5` on port 5173, proxying `/api` to `http://127.0.0.1:8000` unless `MEALFLOW_API_PROXY` is set.
- `docker compose -f docker-compose.s7.yml up --build` is a local integration stack (PostgreSQL, migrations, API, worker), not the uni-app client; it needs `MEALFLOW_LOCAL_DB_PASSWORD` and publishes API on `127.0.0.1:8003`.

## Non-obvious Constraints

- `Settings` resolves `env_file=("../../.env", ".env")` relative to the process working directory, not the repo root. Start API/Alembic/pytest from the repo root or `apps/api` so they read the intended `.env`; `.env` is git-ignored, so never commit credentials.
- Tests force empty LLM keys and replace providers with fakes, so they stay deterministic; do not "fix" this by calling real models.
- Native clients authenticate with Bearer sessions marked `X-MealFlow-Client: native-app`; browser sessions use cookies and writes require CSRF validation. Preserve both paths when touching auth or CORS.
- Inventory quantities are `known` (quantity + unit) or `unknown` (both null; never infer 0 or 1). PATCH, consume, and remove require `expected_version`; only `active` lots may become `consumed` or `removed`, and repeated or cross transitions return 409 without new movements.
- Keep unknown inventory distinct from visual observations. Device images, YOLO results, and climate data are not connected; do not fake them or treat recognition as confirmed stock.
- Agent shared writes (for example procurement) require an explicit confirmation step before side effects.
- Keep frontend and backend code concise. Add checks only at real boundaries; use `try/except` or `try/catch` solely for recovery, translation, cleanup, or user feedback, with a short comment when non-obvious.
- Check `git status` before editing and preserve unrelated in-progress changes.
- Development records are optional. Before each development or modification task, ask `本次开发/修改是否需要填写开发记录？` (or `Should this change be recorded in the development records?`). Create one only when the user agrees for that task. Then add `docs/开发用例/YYYYMMDD-HHMM-brief-change-name.md` in the same change (sequence suffix on collision) with date, scope/files, behavior, verification results, and known gaps; never overwrite or rename earlier records.
- When a task explicitly uses Agent1 for implementation and Agent2 for testing, run exactly: Agent1 implements -> Agent2 tests and documents findings -> Agent1 makes one fix pass from that report -> stop. Do not start another test/fix cycle; report remaining findings as unresolved.
