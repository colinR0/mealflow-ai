"""Queued knowledge rebuilds are idempotent and visible to the worker."""

from sqlalchemy import select

from app.db.session import SessionLocal
from app.db.v2_models import BackgroundJob
from app.knowledge_worker import pending_job_ids
from app.services.native_index_worker import enqueue_rebuild


def test_enqueue_rebuild_reuses_a_runnable_job():
    with SessionLocal() as session:
        first = enqueue_rebuild(session, "profile-test")
        second = enqueue_rebuild(session, "profile-test")
        session.commit()
        jobs = session.scalars(
            select(BackgroundJob).where(
                BackgroundJob.kind == "knowledge_rebuild",
                BackgroundJob.business_id == "profile-test",
            )
        ).all()
    assert first == second
    assert len(jobs) == 1
    assert jobs[0].status == "queued"
    assert first in pending_job_ids()