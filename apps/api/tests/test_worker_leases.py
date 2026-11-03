"""Lease fencing tests; intentionally separate from the general runtime suite."""

from datetime import timedelta
from threading import Event, Thread

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app import worker
from app.db.models import WorkspaceMember
from app.db.session import SessionLocal, engine
from app.db.v2_models import AgentEvent, BackgroundJob, PendingAction, TurnRun, TurnSession
from app.services import v2_runtime as runtime


def create_job(text="hello"):
    with SessionLocal() as session:
        member = session.scalar(select(WorkspaceMember))
        user_id, workspace_id = member.user_id, member.workspace_id
    result = runtime.create_turn(user_id, workspace_id, None, text)
    job_id = worker.claim("worker")
    return user_id, result, job_id


def expire(job_id):
    with SessionLocal() as session, session.begin():
        session.get(BackgroundJob, job_id).lease_until = runtime.utc_now() - timedelta(seconds=1)


def assert_no_output(result):
    with SessionLocal() as session:
        run = session.get(TurnRun, result["run_id"])
        assert run.result == {}
        assert session.scalar(select(func.count()).select_from(PendingAction)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(AgentEvent)
                .where(AgentEvent.run_id == run.id, AgentEvent.type == "completed")
            )
            == 0
        )


def test_snapshot_never_uses_expired_detached_orm(monkeypatch):
    class ExpiringSession(Session):
        def close(self):
            self.expire_all()
            super().close()

    monkeypatch.setattr(
        worker,
        "SessionLocal",
        sessionmaker(bind=engine, class_=ExpiringSession, expire_on_commit=True),
    )
    user_id, result, job_id = create_job("冰箱还有什么")
    worker.execute(job_id, "worker")
    completed = runtime.accessible_run(user_id, result["run_id"])
    assert completed.status == "succeeded"
    assert completed.result["evidence"] == []


def test_heartbeat_renews_but_never_resurrects_expired_lease():
    _, result, job_id = create_job()
    snapshot = worker.snapshot_job(job_id, "worker")
    with SessionLocal() as session, session.begin():
        job = session.get(BackgroundJob, job_id)
        job.lease_until = runtime.utc_now() + timedelta(seconds=10)
        original = runtime.aware(job.lease_until)
    assert worker.heartbeat(snapshot)
    with SessionLocal() as session:
        assert runtime.aware(session.get(BackgroundJob, job_id).lease_until) > original
    expire(job_id)
    assert not worker.heartbeat(snapshot)
    worker.finish(snapshot, {"status": "succeeded", "text": "late"})
    worker.handle_failure(job_id, "worker", snapshot.attempt)
    assert_no_output(result)
    with SessionLocal() as session:
        assert session.get(BackgroundJob, job_id).status == "running"


def test_attempt_fences_same_worker_id_after_reclaim():
    user_id, result, job_id = create_job("买点鸡蛋")
    old = worker.snapshot_job(job_id, "worker")
    expire(job_id)
    assert worker.claim("worker") == job_id
    assert not worker.heartbeat(old)
    worker.finish(old, runtime.request_status(old.text))
    worker.handle_failure(job_id, "worker", old.attempt)
    assert_no_output(result)
    with SessionLocal() as session:
        job = session.get(BackgroundJob, job_id)
        assert job.status == "running" and job.attempts == 2
    worker.execute(job_id, "worker")
    assert runtime.accessible_run(user_id, result["run_id"]).status == "waiting_confirmation"


def test_expiry_during_provider_call_discards_result(monkeypatch):
    _, result, job_id = create_job()

    def provider(_text):
        expire(job_id)
        return {"status": "succeeded", "text": "late"}

    monkeypatch.setattr(worker, "compute_result", provider)
    worker.execute(job_id, "worker")
    assert_no_output(result)


def test_blocked_provider_heartbeats_and_cancels_before_return(monkeypatch):
    user_id, result, job_id = create_job()
    started, release, stopped, renewed = Event(), Event(), Event(), Event()
    errors = []
    original_heartbeat = worker.heartbeat

    def provider(_text):
        started.set()
        assert release.wait(10)
        stopped.set()
        return {"status": "succeeded", "text": "must be discarded"}

    def heartbeat(snapshot):
        value = original_heartbeat(snapshot)
        if started.is_set() and value:
            renewed.set()
        return value

    def execute():
        try:
            worker.execute(job_id, "worker")
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr(worker, "compute_result", provider)
    monkeypatch.setattr(worker, "heartbeat", heartbeat)
    monkeypatch.setattr(worker, "HEARTBEAT_SECONDS", 0.02)
    thread = Thread(target=execute)
    thread.start()
    try:
        assert started.wait(5)
        assert renewed.wait(5)
        runtime.cancel_run(user_id, result["run_id"])
        thread.join(5)
        assert not thread.is_alive(), "Cancellation must not wait for the blocked provider"
        assert not errors
        with SessionLocal() as session:
            run = session.get(TurnRun, result["run_id"])
            assert run.status == "cancelled"
            assert session.get(TurnSession, run.session_id).active_run_id is None
            assert session.get(BackgroundJob, job_id).status == "done"
        assert not stopped.is_set()
    finally:
        release.set()
        thread.join(5)
        assert stopped.wait(5)
    assert runtime.accessible_run(user_id, result["run_id"]).result == {"status": "cancelled"}


def test_provider_failure_retries_and_recovery_is_bounded(monkeypatch):
    user_id, result, job_id = create_job()

    def provider(_text):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(worker, "compute_result", provider)
    for attempt in range(1, 4):
        with pytest.raises(RuntimeError, match="provider unavailable"):
            worker.execute(job_id, "worker")
        with SessionLocal() as session, session.begin():
            job = session.get(BackgroundJob, job_id)
            assert job.attempts == attempt
            assert job.status == ("queued" if attempt < 3 else "done")
            if attempt < 3:
                job.next_run_at = runtime.utc_now()
        if attempt < 3:
            assert worker.claim("worker") == job_id
    assert runtime.accessible_run(user_id, result["run_id"]).error_code == "RETRY_EXHAUSTED"


def test_crashed_claims_and_missing_lease_recover_without_provider(monkeypatch):
    user_id, result, job_id = create_job()
    with SessionLocal() as session, session.begin():
        session.get(BackgroundJob, job_id).lease_until = None
    assert worker.claim("worker") == job_id
    for _ in range(2):
        expire(job_id)
        assert worker.claim("worker") == job_id

    def provider(_text):
        pytest.fail("Exhausted crash recoveries must not call the provider")

    monkeypatch.setattr(worker, "compute_result", provider)
    worker.execute(job_id, "worker")
    assert runtime.accessible_run(user_id, result["run_id"]).error_code == "RETRY_EXHAUSTED"


def test_pre_cancelled_turn_never_calls_provider(monkeypatch):
    user_id, result, job_id = create_job()
    runtime.cancel_run(user_id, result["run_id"])

    def provider(_text):
        pytest.fail("Cancelled work must not start the provider")

    monkeypatch.setattr(worker, "compute_result", provider)
    worker.execute(job_id, "worker")
    assert runtime.accessible_run(user_id, result["run_id"]).status == "cancelled"


def test_finish_rechecks_deadline_after_waiting_for_chat_lock(monkeypatch):
    _, result, job_id = create_job()
    snapshot = worker.snapshot_job(job_id, "worker")
    original_get = Session.get
    original_now = runtime.utc_now
    waited = False

    def get(session, entity, ident, **kwargs):
        nonlocal waited
        value = original_get(session, entity, ident, **kwargs)
        if entity is TurnSession and kwargs.get("with_for_update"):
            waited = True
        return value

    monkeypatch.setattr(Session, "get", get)
    monkeypatch.setattr(
        worker,
        "utc_now",
        lambda: original_now() + timedelta(seconds=worker.LEASE_SECONDS + 1 if waited else 0),
    )
    worker.finish(snapshot, {"status": "succeeded", "text": "late"})
    assert waited
    assert_no_output(result)
