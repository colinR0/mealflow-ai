# Repository Instructions

## Boundaries

- `apps/api/` is the FastAPI service, Alembic migrations, and pytest suite. `apps/web/` is the uni-app client (Vue 3 + TypeScript); it is not the old Vite admin website. `data/seed/` holds seed data and `docs/` holds product/technical baselines and handoff notes.
- The API and all test databases require PostgreSQL. The API checks Alembic head at startup; apply migrations before launching it.

## Commands (PowerShell)

- Install backend/dev dependencies from repo root: `..\.venv\Scripts\python.exe -m pip install -e ".\apps\api[dev]"`.
- Apply migrations from `apps/api/`: `..\..\.venv\Scripts\python.exe -m alembic upgrade head`.
- Run API and worker from `apps/api/`: `..\..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000` and `..\..\.venv\Scripts\python.exe -m app.worker`.
- Run backend tests from repo root only after setting `MEALFLOW_TEST_DATABASE_URL` to a disposable PostgreSQL database whose name ends in `_test`; tests drop and recreate its ORM tables. Example: `$env:MEALFLOW_TEST_DATABASE_URL='postgresql+psycopg://mealflow@127.0.0.1:5434/mealflow_s7_test?gssencmode=disable'; ..\.venv\Scripts\python.exe -m pytest apps/api -q`. Never point this variable at a development/demo database.
- Run backend lint from repo root: `..\.venv\Scripts\python.exe -m ruff check apps/api`.
- In `apps/web/`, use `npm ci`, `npm run typecheck`, and `npm run build:app-android`. HBuilderX imports `apps/web/`; Android CLI output is `apps/web/dist/build/app`. A signed APK requires HBuilderX packaging or Android SDK/signing setup.
- Android emulator API default is `http://10.0.2.2:8000`, configured in `apps/web/src/shared/config.ts`; a physical device needs a reachable development-machine LAN address.
- `docker compose -f docker-compose.s7.yml up --build` starts PostgreSQL, API, and worker only; it does not build or run the uni-app client.

## Non-obvious Constraints

- Avoid defensive programming by default across frontend and backend: do not add redundant null/empty checks, repeated validation, or branches that do not protect a real boundary or define a meaningful fallback. Do not use `try/catch` merely to suppress errors or duplicate lower-layer handling. If one is necessary for recovery, translation, cleanup, or user-facing feedback, add a concise code comment explaining why and call it out in the completion summary.
- Before changing migrations or starting the API, check the configured `DATABASE_URL` and current Alembic head. Do not bypass the schema guard to hide a pending migration.
- The native app's authenticated requests use Bearer sessions marked as native; browser cookie writes retain CSRF validation. Preserve this distinction when changing auth or CORS.
- Keep quantity-unknown inventory distinct from known quantities, and do not present visual observations as confirmed inventory. Device images/vision are not yet connected.
- Product specs in `docs/01_产品需求文档.md` and `docs/02_技术设计文档.md` are target contracts, not proof of implementation; check `docs/README.md` for confirmed decisions and current gaps.
- Check `git status` before editing: this repository may contain substantial in-progress changes. Do not revert unrelated work.
