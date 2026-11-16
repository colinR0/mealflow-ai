"""Run queued knowledge rebuild jobs separately from agent turns.

The API schedules work in ``background_jobs``; this process owns the potentially
slow embedding build and atomically publishes a new active generation.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import UTC, datetime

from sqlalchemy import or_, select

from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.db.v2_models import BackgroundJob
from app.services.native_index_worker import process_index_job
from app.services.retrieval_gateway import configured_embedder, document_embedder

MAX_ATTEMPTS = 3
POLL_SECONDS = 5


def pending_job_ids(limit: int = 10) -> list[str]:
    now = datetime.now(UTC)
    with SessionLocal() as session:
        return list(session.scalars(
            select(BackgroundJob.id).where(
                BackgroundJob.kind == "knowledge_rebuild",
                BackgroundJob.attempts < MAX_ATTEMPTS,
                BackgroundJob.status.in_(("queued", "failed", "running")),
                or_(
                    BackgroundJob.status != "running",
                    BackgroundJob.lease_until.is_(None),
                    BackgroundJob.lease_until <= now,
                ),
            ).order_by(BackgroundJob.next_run_at, BackgroundJob.id).limit(limit)
        ).all())


def run_once() -> list[dict]:
    settings = get_settings()
    provider = configured_embedder(settings)
    results: list[dict] = []
    for job_id in pending_job_ids():
        try:
            with engine.connect() as connection:
                generation_id = process_index_job(
                    connection,
                    job_id,
                    embed=document_embedder(provider),
                    fake=provider.fake,
                    model=provider.model,
                    count_tokens=provider.count_tokens,
                )
            results.append({"job_id": job_id, "generation_id": generation_id})
        except RuntimeError as error:
            if "already running" in str(error):
                continue
            logging.exception("Knowledge rebuild failed: %s", job_id)
            results.append({"job_id": job_id, "error": str(error)})
        except Exception as error:  # Keep the polling worker alive; the job is marked failed.
            logging.exception("Knowledge rebuild failed: %s", job_id)
            results.append({"job_id": job_id, "error": str(error)})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Process queued knowledge rebuild jobs.")
    parser.add_argument("--once", action="store_true", help="process one polling pass and exit")
    args = parser.parse_args()

    from app.db.schema_guard import ensure_schema_current

    ensure_schema_current()
    if args.once:
        print(json.dumps(run_once(), ensure_ascii=False))
        return
    while True:
        try:
            for result in run_once():
                print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception:  # A transient DB outage must not kill the polling process.
            logging.exception("Knowledge worker polling failed")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()