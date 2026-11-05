import os
import re
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

# Tests require an explicitly provisioned, disposable PostgreSQL database.
test_database_url = os.environ.get("MEALFLOW_TEST_DATABASE_URL")
if not test_database_url:
    raise RuntimeError("Set MEALFLOW_TEST_DATABASE_URL to a disposable PostgreSQL database")
if not re.match(r"^postgresql(?:\+psycopg)?://", test_database_url):
    raise RuntimeError("MEALFLOW_TEST_DATABASE_URL must use PostgreSQL")
test_database_engine = create_engine(test_database_url)
database_name = test_database_engine.url.database
if database_name is None or not database_name.endswith("_test"):
    raise RuntimeError("MEALFLOW_TEST_DATABASE_URL database name must end with _test")
test_database_engine.dispose()
os.environ["DATABASE_URL"] = test_database_url
os.environ["LLM_API_KEY"] = ""
os.environ["AITECHFLUX_API_KEY"] = ""
os.environ["BIGMODEL_API_KEY"] = ""

# Configure the isolated database before importing application modules.
from app.db.seed import seed_demo_data  # noqa: E402


@pytest.fixture(autouse=True)
def _database_schema():
    """Recreate tables only in the explicitly named disposable PostgreSQL test database."""
    from app.db import models  # noqa: F401
    from app.db.base import Base
    from app.db.session import engine

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"
        ))
        connection.execute(text("DELETE FROM alembic_version"))
        # metadata creates the current schema; record its real heads for the startup guard.
        api_root = Path(__file__).resolve().parents[1]
        config = Config(str(api_root / "alembic.ini"))
        config.set_main_option("script_location", str(api_root / "migrations"))
        head = ScriptDirectory.from_config(config).get_current_head()
        if head is None:
            raise RuntimeError("Expected one Alembic head for the ORM test schema")
        connection.execute(text(
            "INSERT INTO alembic_version (version_num) VALUES (:head)"
        ), {"head": head})


@pytest.fixture(autouse=True)
def _seed_demo_data():
    seed_demo_data()


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    """单测不打真实云端模型，始终走规则基线路径。"""
    from app.services import agent_service

    monkeypatch.setattr(agent_service, "build_llm_provider", lambda settings: None)


@pytest.fixture(autouse=True)
def _offline_worker_provider(monkeypatch):
    """Worker regression tests remain deterministic and never call paid models."""
    from app.agent.durable_graph import FakeProvider
    from app.llm import durable_provider

    monkeypatch.setattr(durable_provider, "ConfiguredProvider", FakeProvider)
