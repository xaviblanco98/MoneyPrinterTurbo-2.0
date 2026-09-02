"""Durable job queue backed by the ``pipeline_jobs`` table.

Design goals for H1: every job persists its state, enqueueing is idempotent
by key, failures store a structured error, retries use exponential backoff
with a fixed maximum, and a restart recovers jobs whose worker died.

Stage handlers are plain callables ``handler(job, session) -> dict | None``.
They are registered per stage name; the queue itself contains no stage logic.
"""

from __future__ import annotations

import hashlib
import json
import socket
import os
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from mpt2.errors import ErrorInfo, StageError, utcnow
from mpt2.models import PipelineJob
from mpt2.models.enums import JobStatus

Handler = Callable[[PipelineJob, Session], "dict[str, Any] | None"]


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def make_idempotency_key(
    project_id: str, stage: str, payload: Mapping[str, Any] | None
) -> str:
    """Stable key: same project + stage + payload means the same job."""
    canonical = json.dumps(
        payload or {}, sort_keys=True, separators=(",", ":"), default=str
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{project_id}:{stage}:{digest}"


class JobQueue:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        max_attempts: int = 3,
        retry_base_seconds: float = 5.0,
        stale_lock_seconds: int = 900,
        worker_id: str | None = None,
    ):
        self._factory = session_factory
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.stale_lock_seconds = stale_lock_seconds
        self.worker_id = worker_id or default_worker_id()
        self._handlers: dict[str, Handler] = {}

    # ---------------------------------------------------------- registry
    def register(self, stage: str, handler: Handler) -> None:
        self._handlers[stage] = handler

    def handler_for(self, stage: str) -> Handler | None:
        return self._handlers.get(stage)

    # ----------------------------------------------------------- enqueue
    def enqueue(
        self,
        session: Session,
        project_id: str,
        stage: str,
        payload: Mapping[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
        max_attempts: int | None = None,
        run_at: datetime | None = None,
    ) -> PipelineJob:
        """Create a job or return the existing one with the same key."""
        key = idempotency_key or make_idempotency_key(project_id, stage, payload)
        existing = session.scalar(
            select(PipelineJob).where(PipelineJob.idempotency_key == key)
        )
        if existing is not None:
            return existing
        job = PipelineJob(
            project_id=project_id,
            stage=stage,
            status=JobStatus.queued,
            idempotency_key=key,
            payload=dict(payload or {}),
            max_attempts=max_attempts or self.max_attempts,
            next_run_at=run_at or utcnow(),
        )
        session.add(job)
        session.flush()
        return job

    # ------------------------------------------------------------- claim
    def claim_next(
        self, session: Session, *, now: datetime | None = None
    ) -> PipelineJob | None:
        """Atomically take the oldest runnable job for this worker."""
        moment = now or utcnow()
        candidates = session.scalars(
            select(PipelineJob)
            .where(
                PipelineJob.status == JobStatus.queued,
                PipelineJob.next_run_at <= moment,
            )
            .order_by(PipelineJob.next_run_at, PipelineJob.created_at)
            .limit(1)
            .with_for_update()
        ).all()
        if not candidates:
            return None
        job = candidates[0]
        job.status = JobStatus.running
        job.locked_by = self.worker_id
        job.locked_at = moment
        job.started_at = moment
        job.attempts += 1
        session.flush()
        return job

    def mark_done(
        self,
        session: Session,
        job: PipelineJob,
        result: Mapping[str, Any] | None = None,
    ) -> None:
        job.status = JobStatus.done
        job.result = dict(result or {})
        job.finished_at = utcnow()
        job.locked_by = None
        job.locked_at = None
        job.error_code = None
        job.error_message = None
        job.error_module = None
        job.error_at = None
        session.flush()

    def mark_failed(
        self,
        session: Session,
        job: PipelineJob,
        error: ErrorInfo,
        *,
        retryable: bool = True,
        now: datetime | None = None,
    ) -> None:
        """Record the error; requeue with backoff while attempts remain."""
        moment = now or utcnow()
        job.error_code = error.code
        job.error_message = error.message
        job.error_module = error.module
        job.error_at = error.occurred_at
        job.locked_by = None
        job.locked_at = None
        if retryable and job.attempts < job.max_attempts:
            delay = self.retry_base_seconds * (2 ** (job.attempts - 1))
            job.status = JobStatus.queued
            job.next_run_at = moment + timedelta(seconds=delay)
        else:
            job.status = JobStatus.failed
            job.finished_at = moment
        session.flush()

    def escalate(self, session: Session, job: PipelineJob, reason: str) -> None:
        job.status = JobStatus.needs_human
        job.error_code = job.error_code or "needs_human"
        job.error_message = reason
        job.error_module = job.error_module or __name__
        job.error_at = utcnow()
        job.locked_by = None
        job.locked_at = None
        session.flush()

    # ---------------------------------------------------------- recovery
    def recover_stale(self, session: Session, *, now: datetime | None = None) -> int:
        """After a crash: jobs still ``running`` past the lock timeout go back to the queue."""
        moment = now or utcnow()
        cutoff = moment - timedelta(seconds=self.stale_lock_seconds)
        stale = session.scalars(
            select(PipelineJob).where(
                PipelineJob.status == JobStatus.running, PipelineJob.locked_at <= cutoff
            )
        ).all()
        for job in stale:
            error = ErrorInfo(
                code="worker_lost",
                message=f"worker {job.locked_by or '?'} did not finish the job",
                module=__name__,
                occurred_at=moment,
            )
            self.mark_failed(session, job, error, now=moment)
        return len(stale)

    # --------------------------------------------------------------- run
    def run_one(self, *, now: datetime | None = None) -> PipelineJob | None:
        """Claim and execute a single job in its own transaction. Returns it or None."""
        with self._factory() as session:
            job = self.claim_next(session, now=now)
            if job is None:
                session.rollback()
                return None
            session.commit()
            job_id = job.id

        with self._factory() as session:
            job = session.get(PipelineJob, job_id)
            handler = self._handlers.get(job.stage)
            if handler is None:
                self.mark_failed(
                    session,
                    job,
                    ErrorInfo(
                        code="no_handler",
                        message=f"no handler registered for stage {job.stage!r}",
                        module=__name__,
                    ),
                    retryable=False,
                )
                session.commit()
                return job
            try:
                result = handler(job, session)
            except StageError as exc:
                session.rollback()
                job = session.get(PipelineJob, job_id)
                self.mark_failed(session, job, exc.to_info(), retryable=exc.retryable)
            except Exception as exc:  # noqa: BLE001 - any failure must be persisted
                session.rollback()
                job = session.get(PipelineJob, job_id)
                self.mark_failed(
                    session,
                    job,
                    ErrorInfo(
                        code="unexpected_error",
                        message=f"{type(exc).__name__}: {exc}",
                        module=getattr(handler, "__module__", __name__),
                    ),
                )
            else:
                self.mark_done(session, job, result)
            session.commit()
            return job

    def run_pending(self, *, max_jobs: int = 100, now: datetime | None = None) -> int:
        """Run runnable jobs until none is left or ``max_jobs`` is reached."""
        count = 0
        while count < max_jobs:
            if self.run_one(now=now) is None:
                break
            count += 1
        return count
