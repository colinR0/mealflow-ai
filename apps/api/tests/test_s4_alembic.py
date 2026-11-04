"""Exercise historical migrations plus the new revision, never application schemas."""
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

API_ROOT = Path(__file__).resolve().parents[1]


def test_full_alembic_upgrade_and_admission_seed():
    raw = os.environ.get("MEALFLOW_TEST_POSTGRES_URL")
    if not raw:
        pytest.skip("PostgreSQL integration URL required")
    admin = create_engine(raw)
    assert admin.dialect.name == "postgresql"
    schema = "test_s4_migration_" + uuid.uuid4().hex
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    url = make_url(raw).update_query_dict({"options": f"-csearch_path={schema}"})
    database_url = url.render_as_string(hide_password=False)
    engine = create_engine(database_url)
    try:
        env = {**os.environ, "DATABASE_URL": database_url}
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=API_ROOT, env=env, text=True, capture_output=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr
        with engine.connect() as connection:
            assert connection.scalar(text("select version_num from alembic_version")) == (
                "3c6d8e0f1a2b")
            assert connection.scalar(text("select id from runtime_admission")) == "agent_turn"
        tables = inspect(engine).get_table_names()
        assert {"inventory_items", "users", "household_profiles"} <= set(tables)
        assert "legacy_imports" not in tables
        auth_columns = {column["name"] for column in inspect(engine).get_columns("auth_sessions")}
        assert "native_client" in auth_columns
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()
