r"""Explicit opt-in only; never create/drop tables in the application's schema.

PowerShell:
  $env:MEALFLOW_TEST_POSTGRES_URL = 'postgresql+psycopg://mealflow@localhost:5433/mealflow_dev'
  .\.venv\Scripts\python.exe -m pytest apps/api/tests/test_runtime_postgres_concurrency.py -q
"""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.base import Base
from app.db.models import User, Workspace, WorkspaceMember
from app.db.v2_models import (
    AgentEvent,
    AuditEvent,
    BackgroundJob,
    IdempotencyRecord,
    PendingAction,
    ShoppingItem,
    TurnRun,
    TurnSession,
)
from app.services import v2_runtime as runtime


@pytest.fixture
def postgres_runtime(monkeypatch):
    url = os.environ.get("MEALFLOW_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set MEALFLOW_TEST_POSTGRES_URL to opt into isolated PostgreSQL tests")
    admin = create_engine(url, connect_args={"connect_timeout": 5})
    if admin.dialect.name != "postgresql":
        admin.dispose()
        pytest.fail("MEALFLOW_TEST_POSTGRES_URL must point to PostgreSQL")
    schema = f"test_runtime_{uuid.uuid4().hex}"
    created = False
    engine = admin.execution_options(schema_translate_map={None: schema})
    try:
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        created = True
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        with sessions.begin() as session:
            user = User(email="race@example.invalid", display_name="Race", password_hash="unused")
            workspace = Workspace(name="Isolated race", kind="personal")
            session.add_all([user, workspace])
            session.flush()
            session.add(WorkspaceMember(
                workspace_id=workspace.id, user_id=user.id, role="owner", status="active",
            ))
            actor = user.id, workspace.id
        monkeypatch.setattr(runtime, "SessionLocal", sessions)
        yield engine, sessions, actor
    finally:
        # The randomly generated schema is the only object this fixture may drop.
        if created:
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def race_inserts(engine, first, second):
    """Both transactions see a miss before either unique insert can complete."""
    barrier = Barrier(2)
    lock = Lock()
    backend_pids = set()
    inserts = []

    def before_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO") and (
            "idempotency_records" in statement
        ):
            with lock:
                backend_pids.add(connection.connection.driver_connection.info.backend_pid)
                inserts.append(statement)
            barrier.wait(timeout=10)

    def call(function):
        try:
            return function()
        except HTTPException as error:  # Compare both concurrent calls' expected API outcomes.
            return error

    event.listen(engine, "before_cursor_execute", before_insert)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(call, function) for function in (first, second)]
            results = [future.result(timeout=20) for future in futures]
    finally:
        event.remove(engine, "before_cursor_execute", before_insert)
    assert len(backend_pids) == 2, "Must exercise separate PostgreSQL connections"
    assert len(inserts) == 2, "Both calls must attempt the conflicting insert"
    return results


def assert_conflict_pair(results):
    errors = [result for result in results if isinstance(result, HTTPException)]
    assert len(errors) == 1
    assert errors[0].status_code == 409
    assert errors[0].detail == {"code": "IDEMPOTENCY_CONFLICT"}
    return next(result for result in results if isinstance(result, dict))


@pytest.mark.parametrize("different_request", [False, True])
@pytest.mark.parametrize("existing_session", [False, True])
def test_concurrent_turn_key(postgres_runtime, different_request, existing_session):
    engine, sessions, (user_id, workspace_id) = postgres_runtime
    session_id = None
    if existing_session:
        with sessions.begin() as session:
            chat = TurnSession(workspace_id=workspace_id, created_by=user_id, state={})
            session.add(chat)
            session.flush()
            session_id = chat.id

    def create(text):
        return runtime.create_turn(
            user_id, workspace_id, session_id, text, idempotency_key="turn-key",
        )

    results = race_inserts(engine, lambda: create("first"),
                           lambda: create("second" if different_request else "first"))
    if different_request:
        winner = assert_conflict_pair(results)
    else:
        assert results[0] == results[1]
        winner = results[0]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(TurnRun)) == 1
        assert session.scalar(select(func.count()).select_from(TurnSession)) == 1
        assert session.scalar(select(func.count()).select_from(BackgroundJob)) == 1
        assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1
        run = session.get(TurnRun, winner["run_id"])
        text = run.request["text"]
    assert create(text) == winner


@pytest.mark.parametrize("different_action", [False, True])
def test_concurrent_action_key(postgres_runtime, different_action):
    engine, sessions, (user_id, workspace_id) = postgres_runtime
    first = runtime.prepare_action(user_id, workspace_id, "add_procurement", {"name": "egg"})
    second = runtime.prepare_action(
        user_id, workspace_id, "add_procurement", {"name": "milk"},
    ) if different_action else first

    def decide(action_id):
        return runtime.decide_action(user_id, action_id, True, "action-key")

    results = race_inserts(engine, lambda: decide(first["id"]), lambda: decide(second["id"]))
    if different_action:
        winner = assert_conflict_pair(results)
    else:
        assert results[0] == results[1]
        winner = results[0]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ShoppingItem)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1
        # These fixture actions are standalone (run_id=None), so no run event is
        # emitted. Attached actions are covered by the HTTP decision tests.
        assert session.scalar(select(func.count()).select_from(AgentEvent).where(
            AgentEvent.type == "action_decided"
        )) == 0
        assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1
        if different_action:
            loser_id = next(action["id"] for action in (first, second)
                            if action["id"] != winner["action_id"])
            assert session.get(PendingAction, loser_id).status == "pending"
    assert decide(winner["action_id"]) == winner


def test_decided_action_replay_reserves_new_key(postgres_runtime):
    _, sessions, (user_id, workspace_id) = postgres_runtime
    first = runtime.prepare_action(user_id, workspace_id, "add_procurement", {"name": "egg"})
    second = runtime.prepare_action(user_id, workspace_id, "add_procurement", {"name": "milk"})
    result = runtime.decide_action(user_id, first["id"], True, "original")
    assert runtime.decide_action(user_id, first["id"], True, "alias") == result
    with pytest.raises(HTTPException) as error:
        runtime.decide_action(user_id, second["id"], True, "alias")
    assert error.value.detail == {"code": "IDEMPOTENCY_CONFLICT"}
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ShoppingItem)) == 1
