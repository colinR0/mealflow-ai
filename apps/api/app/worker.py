"""Durable, lease-fenced LangGraph worker. Run separately: python -m app.worker."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from queue import Empty, Queue
from threading import Thread

from fastapi import HTTPException
from sqlalchemy import or_, select

from app.agent import durable_graph
from app.db.session import SessionLocal
from app.db.v2_models import BackgroundJob, PendingAction, TurnRun, TurnSession
from app.services.v2_runtime import (
    append_event,
    authorized_workspace,
    aware,
    canonical_hash,
    utc_now,
)

LEASE_SECONDS = 60
HEARTBEAT_SECONDS = 5
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class JobSnapshot:
    job_id: str
    worker_id: str
    attempt: int
    run_id: str
    workspace_id: str
    text: str


def owns_lease(job: BackgroundJob | None, worker_id: str, attempt: int | None = None) -> bool:
    return bool(
        job is not None
        and job.worker_id == worker_id
        and job.status == "running"
        and job.lease_until is not None
        and aware(job.lease_until) > utc_now()
        and (attempt is None or job.attempts == attempt)
    )


def claim(worker_id: str) -> str | None:
    now = utc_now()
    with SessionLocal() as session, session.begin():
        job = session.scalar(
            select(BackgroundJob)
            .where(
                BackgroundJob.kind == "agent_turn",
                BackgroundJob.next_run_at <= now,
                or_(
                    BackgroundJob.status == "queued",
                    (BackgroundJob.status == "running")
                    & or_(BackgroundJob.lease_until <= now, BackgroundJob.lease_until.is_(None)),
                ),
            )
            .order_by(BackgroundJob.next_run_at, BackgroundJob.id)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        job.worker_id = worker_id
        job.lease_until = now + timedelta(seconds=LEASE_SECONDS)
        return job.id


def snapshot_job(job_id: str, worker_id: str) -> JobSnapshot | None:
    with SessionLocal() as session, session.begin():
        job = session.get(BackgroundJob, job_id, with_for_update=True)
        if not owns_lease(job, worker_id):
            return None
        run = session.get(TurnRun, job.business_id, with_for_update=True)
        if not owns_lease(job, worker_id):
            return None
        if run is None:
            job.status, job.lease_until = "done", None
            return None
        snapshot = JobSnapshot(
            job.id, worker_id, job.attempts, run.id, run.workspace_id, run.request["text"]
        )
        if run.status == "queued" and not run.cancel_requested:
            run.status = "running"
            append_event(session, run, "status", {"status": "running"})
        return snapshot


def heartbeat(snapshot: JobSnapshot) -> bool:
    """Never resurrect an expired lease, even if nobody has reclaimed it yet."""
    with SessionLocal() as session, session.begin():
        job = session.get(BackgroundJob, snapshot.job_id, with_for_update=True)
        if not owns_lease(job, snapshot.worker_id, snapshot.attempt):
            return False
        run = session.get(TurnRun, snapshot.run_id)
        if run is None or run.cancel_requested or run.status not in {"queued", "running"}:
            return False
        try:
            authorized_workspace(session, run.actor_id, run.workspace_id)
        except HTTPException:  # Revoked access stops lease renewal and further execution.
            return False
        job.lease_until = utc_now() + timedelta(seconds=LEASE_SECONDS)
        return True


def compute_result(snapshot: JobSnapshot) -> dict:
    from app.llm.durable_provider import ConfiguredProvider

    return durable_graph.compute(SessionLocal, snapshot, provider=ConfiguredProvider())


def remaining_seconds(snapshot: JobSnapshot) -> float:
    with SessionLocal() as session:
        run = session.get(TurnRun, snapshot.run_id)
        return (
            durable_graph.RUN_TIMEOUT_SECONDS - (utc_now() - aware(run.created_at)).total_seconds()
        )


def execute(job_id: str, worker_id: str) -> None:
    snapshot = snapshot_job(job_id, worker_id)
    if snapshot is None:
        return
    try:
        if snapshot.attempt > MAX_ATTEMPTS or not heartbeat(snapshot):
            finish(snapshot, {})
            return
        deadline = time.monotonic() + remaining_seconds(snapshot)
        if deadline <= time.monotonic():
            finish(snapshot, {"status": "failed", "error_code": "RUN_TIMEOUT"})
            return
        output: Queue = Queue(maxsize=1)

        def compute() -> None:
            try:
                output.put((True, compute_result(snapshot)))
            except Exception as error:  # Return provider failure to the worker thread.
                output.put((False, error))

        # A blocked provider must not block cancellation or hold a DB transaction.
        # Graph/checkpoint/tool writes independently check this same lease and attempt.
        Thread(target=compute, daemon=True, name=f"turn-{job_id}").start()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                finish(snapshot, {"status": "failed", "error_code": "RUN_TIMEOUT"})
                return
            try:
                succeeded, result = output.get(timeout=min(HEARTBEAT_SECONDS, remaining))
            except Empty:  # Keep renewing the lease while provider computation is pending.
                if not heartbeat(snapshot):
                    finish(snapshot, {})
                    return
                continue
            if not succeeded:
                raise result
            finish(snapshot, result)
            return
    except durable_graph.RunTimedOut:  # Persist a terminal timeout result for the client.
        finish(snapshot, {"status": "failed", "error_code": "RUN_TIMEOUT"})
    except durable_graph.ExecutionStopped as error:  # Finish using current lease ownership.
        if heartbeat(snapshot):
            finish(snapshot, {"status": "failed", "error_code": str(error)})
        else:
            finish(snapshot, {})
    except Exception:  # Persist retry state before propagating computation failure.
        try:
            handle_failure(job_id, worker_id, snapshot.attempt)
        except Exception:
            # A DB outage must not kill the polling process. Expiry permits recovery.
            logging.exception("Could not persist retry; lease will recover: %s", job_id)
        raise


def finish(snapshot: JobSnapshot, result: dict) -> None:
    with SessionLocal() as session, session.begin():
        job = session.get(BackgroundJob, snapshot.job_id, with_for_update=True)
        if not owns_lease(job, snapshot.worker_id, snapshot.attempt):
            return  # Another lease owns the result; discard late output.
        run = session.get(TurnRun, job.business_id, with_for_update=True)
        if run is None or run.status not in {"queued", "running"}:
            job.status, job.lease_until = "done", None
            return
        chat = session.get(TurnSession, run.session_id, with_for_update=True)
        # Locks may have blocked past the deadline even though we owned the job above.
        if not owns_lease(job, snapshot.worker_id, snapshot.attempt):
            return
        try:
            authorized_workspace(session, run.actor_id, run.workspace_id)
            allowed = True
        except HTTPException:  # Translate revoked authorization into a terminal job result.
            allowed = False
        if run.cancel_requested:
            run.status, run.result = "cancelled", {"status": "cancelled"}
            append_event(session, run, "cancelled", run.result)
        elif not allowed or job.attempts > MAX_ATTEMPTS:
            run.status = "failed"
            run.error_code = "ACCESS_REVOKED" if not allowed else "RETRY_EXHAUSTED"
            run.result = {"status": "failed", "error_code": run.error_code}
            append_event(session, run, "failed", run.result)
        else:
            if (
                utc_now() - aware(run.created_at)
            ).total_seconds() >= durable_graph.RUN_TIMEOUT_SECONDS:
                result = {"status": "failed", "error_code": "RUN_TIMEOUT"}
            state = result.pop("_state", None)
            action = result.pop("action", None)
            if action:
                row = PendingAction(
                    workspace_id=run.workspace_id,
                    actor_id=run.actor_id,
                    run_id=run.id,
                    type=action["type"],
                    payload=action["payload"],
                    payload_hash=canonical_hash(action["payload"]),
                    version=1,
                    status="pending",
                    expires_at=utc_now() + timedelta(hours=24),
                    result={},
                )
                session.add(row)
                session.flush()
                result["pending_action"] = {
                    "id": row.id,
                    "type": row.type,
                    "payload": row.payload,
                    "payload_hash": row.payload_hash,
                    "version": row.version,
                }
                append_event(session, run, "action_required", result["pending_action"])
            run.status, run.result = result["status"], result
            run.error_code = result.get("error_code")
            run.state = state or {
                "provider": result.get("provider", "FakeProvider"),
                "constraints": chat.state.get("constraints", {}),
                "pending_action_id": result.get("pending_action", {}).get("id"),
                "budgets": {"tool_calls": 0, "model_calls": 0},
            }
            run.state = {
                **run.state,
                "pending_action_id": result.get("pending_action", {}).get("id"),
            }
            chat.state = run.state
            append_event(session, run, "completed", result)
        if chat.active_run_id == run.id:
            chat.active_run_id = None
        job.status, job.lease_until = "done", None


def handle_failure(job_id: str, worker_id: str, attempt: int | None = None) -> None:
    """Persist bounded retry state after an exception outside the job transaction."""
    with SessionLocal() as session, session.begin():
        job = session.get(BackgroundJob, job_id, with_for_update=True)
        if not owns_lease(job, worker_id, attempt):
            return
        run = session.get(TurnRun, job.business_id, with_for_update=True)
        if not owns_lease(job, worker_id, attempt):
            return
        if run is not None and run.cancel_requested:
            run.status, run.result = "cancelled", {"status": "cancelled"}
            append_event(session, run, "cancelled", run.result)
            chat = session.get(TurnSession, run.session_id, with_for_update=True)
            if chat is not None and chat.active_run_id == run.id:
                chat.active_run_id = None
            job.status, job.lease_until = "done", None
            return
        if run is None or run.status not in {"queued", "running"}:
            job.status, job.lease_until = "done", None
            return
        if job.attempts < MAX_ATTEMPTS:
            job.status = "queued"
            job.next_run_at = utc_now() + timedelta(seconds=min(30, 2**job.attempts))
            job.lease_until = None
            return
        if run is not None and run.status in {"queued", "running"}:
            run.status = "failed"
            run.error_code = "RETRY_EXHAUSTED"
            run.result = {"status": "failed", "error_code": run.error_code}
            append_event(session, run, "failed", run.result)
            chat = session.get(TurnSession, run.session_id, with_for_update=True)
            if chat is not None and chat.active_run_id == run.id:
                chat.active_run_id = None
        job.status, job.lease_until = "done", None


def main() -> None:
    from app.db.schema_guard import ensure_schema_current

    ensure_schema_current()
    worker_id = str(uuid.uuid4())
    while True:
        try:
            job_id = claim(worker_id)
            if job_id:
                execute(job_id, worker_id)
            else:
                time.sleep(1)
        except Exception:  # Keep polling after a failed iteration so expired leases can recover.
            logging.exception("Demo worker iteration failed; retrying after backoff")
            time.sleep(1)


if __name__ == "__main__":
    main()
