"""Run the complete migration chain in a fresh disposable database, never a shared schema."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

API_ROOT = Path(__file__).resolve().parents[1]


def test_full_alembic_upgrade_and_admission_seed():
    raw = os.environ.get("MEALFLOW_TEST_POSTGRES_URL")
    if not raw:
        pytest.skip("PostgreSQL integration URL required")
    source = make_url(raw)
    assert source.drivername in {"postgresql", "postgresql+psycopg"}
    assert source.database.endswith("_test")
    # Migrations create named schemas/extensions, so a search_path-only sandbox is insufficient.
    name = "migration_" + uuid.uuid4().hex + "_test"
    admin = create_engine(source, isolation_level="AUTOCOMMIT")
    url = source.set(database=name).update_query_dict({"options": "-csearch_path=public"})
    database_url = url.render_as_string(hide_password=False)
    engine = create_engine(url)
    created = False
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        created = True
        env = {**os.environ, "DATABASE_URL": database_url}
        for arguments in (["current"], ["heads"], ["upgrade", "head"]):
            result = subprocess.run(
                [sys.executable, "-m", "alembic", *arguments],
                cwd=API_ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=60,
            )
            assert result.returncode == 0, result.stderr
        config = Config(str(API_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(API_ROOT / "migrations"))
        expected_head = ScriptDirectory.from_config(config).get_current_head()
        with engine.connect() as connection:
            assert (
                connection.scalar(text("select version_num from alembic_version")) == expected_head
            )
            assert connection.scalar(text("select id from runtime_admission")) == "agent_turn"
        tables = set(inspect(engine).get_table_names())
        assert tables == {
            "users",
            "workspaces",
            "workspace_members",
            "ingredients",
            "ingredient_aliases",
            "auth_sessions",
            "login_attempts",
            "household_profiles",
            "household_invites",
            "inventory_lots",
            "inventory_movements",
            "shopping_items",
            "turn_sessions",
            "agent_turn_runs",
            "agent_events",
            "tool_executions",
            "pending_actions",
            "background_jobs",
            "idempotency_records",
            "audit_events",
            "runtime_admission",
            "recipe_source_assets",
            "recipe_source_asset_versions",
            "alembic_version",
        }
        assert set(inspect(engine).get_table_names(schema="recipe_research")) == {
            "parents",
            "chunks",
            "build_batches",
        }
        auth_columns = {column["name"] for column in inspect(engine).get_columns("auth_sessions")}
        assert "native_client" in auth_columns
    finally:
        engine.dispose()
        if created:
            # The fresh UUID-named _test database is the only database this test may remove.
            assert name.startswith("migration_") and name.endswith("_test")
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()
