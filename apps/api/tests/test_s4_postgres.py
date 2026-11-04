"""Real two-connection admission races; isolated random schemas only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import pytest
from fastapi import HTTPException
from sqlalchemy import event, func, select
from test_runtime_postgres_concurrency import postgres_runtime as runtime_fixture

from app.core.config import get_settings
from app.db.v2_models import BackgroundJob, IdempotencyRecord, TurnRun
from app.services import v2_runtime as runtime

postgres_runtime = runtime_fixture


@pytest.mark.parametrize("global_capacity", [False, True])
def test_real_postgres_admission_race(postgres_runtime, monkeypatch, global_capacity):
    engine, sessions, (user, household) = postgres_runtime
    settings = get_settings()
    monkeypatch.setattr(settings, "runtime_household_active_limit", 10 if global_capacity else 1)
    monkeypatch.setattr(settings, "runtime_queue_capacity", 1 if global_capacity else 10)
    second_household = runtime.create_household(user, "Other") if global_capacity else household
    barrier, lock = Barrier(2), Lock()
    pids = set()

    def before_gate(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE") and "runtime_admission" in statement:
            with lock:
                pids.add(connection.connection.driver_connection.info.backend_pid)
            barrier.wait(timeout=10)

    def create(index, target):
        try:
            return runtime.create_turn(user, target, None, str(index), idempotency_key=str(index))
        except HTTPException as error:  # Collect both admission race outcomes for assertions.
            return error

    event.listen(engine, "before_cursor_execute", before_gate)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create, 0, household),
                       pool.submit(create, 1, second_household)]
            results = [future.result(timeout=20) for future in futures]
    finally:
        event.remove(engine, "before_cursor_execute", before_gate)
    assert len(pids) == 2
    errors = [result for result in results if isinstance(result, HTTPException)]
    assert len(errors) == 1
    assert errors[0].detail["code"] == (
        "QUEUE_FULL" if global_capacity else "HOUSEHOLD_RUN_LIMIT")
    with sessions() as session:
        for model in (TurnRun, BackgroundJob, IdempotencyRecord):
            assert session.scalar(select(func.count()).select_from(model)) == 1
