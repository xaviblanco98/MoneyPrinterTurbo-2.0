from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from mpt2 import db as dbmod
from mpt2 import services
from mpt2.errors import ErrorInfo, StageError, utcnow
from mpt2.jobs import JobQueue, make_idempotency_key
from mpt2.models import PipelineJob
from mpt2.models.enums import JobStatus


@pytest.fixture
def project_id(session_factory, channel_config):
    with dbmod.session_scope(session_factory) as session:
        channel = services.create_channel(session, channel_config)
        project = services.create_project(session, channel, title="T", topic="topic")
        return project.id


@pytest.fixture
def queue(session_factory):
    return JobQueue(
        session_factory,
        max_attempts=3,
        retry_base_seconds=0,
        stale_lock_seconds=60,
        worker_id="w1",
    )


def test_idempotency_key_is_stable():
    a = make_idempotency_key("p", "research", {"b": 1, "a": 2})
    b = make_idempotency_key("p", "research", {"a": 2, "b": 1})
    c = make_idempotency_key("p", "research", {"a": 3})
    assert a == b != c


def test_enqueue_is_idempotent(queue, session_factory, project_id):
    with dbmod.session_scope(session_factory) as session:
        first = queue.enqueue(session, project_id, "research", {"topic": "x"})
        second = queue.enqueue(session, project_id, "research", {"topic": "x"})
        third = queue.enqueue(session, project_id, "research", {"topic": "y"})
        assert first.id == second.id != third.id
        assert session.scalar(select(PipelineJob.id).where(PipelineJob.id == first.id))


def test_success_path_stores_result(queue, session_factory, project_id):
    seen = []

    def handler(job, session):
        seen.append(job.payload)
        return {"ok": True}

    queue.register("research", handler)
    with dbmod.session_scope(session_factory) as session:
        job = queue.enqueue(session, project_id, "research", {"topic": "x"})
        job_id = job.id
    assert queue.run_pending() == 1
    assert seen == [{"topic": "x"}]
    with session_factory() as session:
        job = session.get(PipelineJob, job_id)
        assert job.status == JobStatus.done
        assert job.result == {"ok": True}
        assert job.attempts == 1
        assert job.locked_by is None and job.finished_at is not None


def test_failure_retries_with_backoff_then_fails(queue, session_factory, project_id):
    calls = {"n": 0}

    def flaky(job, session):
        calls["n"] += 1
        raise StageError(
            "provider timeout", code="llm_timeout", module="mpt2.agents.research"
        )

    queue.register("research", flaky)
    with dbmod.session_scope(session_factory) as session:
        job_id = queue.enqueue(session, project_id, "research").id

    assert queue.run_one() is not None
    with session_factory() as session:
        job = session.get(PipelineJob, job_id)
        assert job.status == JobStatus.queued
        assert job.attempts == 1
        assert (job.error_code, job.error_module) == (
            "llm_timeout",
            "mpt2.agents.research",
        )
        assert job.error_message == "provider timeout" and job.error_at is not None

    queue.run_pending()
    with session_factory() as session:
        job = session.get(PipelineJob, job_id)
        assert job.status == JobStatus.failed
        assert job.attempts == 3
    assert calls["n"] == 3
    errors = None
    with session_factory() as session:
        errors = services.project_errors(session, project_id)
    assert errors[0]["code"] == "llm_timeout"


def test_backoff_delays_next_run(session_factory, project_id):
    queue = JobQueue(session_factory, retry_base_seconds=10, worker_id="w")
    queue.register(
        "s", lambda job, session: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    with dbmod.session_scope(session_factory) as session:
        job_id = queue.enqueue(session, project_id, "s").id
    now = utcnow()
    queue.run_one(now=now)
    with session_factory() as session:
        job = session.get(PipelineJob, job_id)
        assert job.error_code == "unexpected_error"
        assert job.next_run_at - now >= timedelta(seconds=9)
    # Not runnable yet at the same instant, runnable later.
    assert queue.run_one(now=now) is None
    assert queue.run_one(now=now + timedelta(seconds=11)) is not None


def test_non_retryable_error_fails_immediately(queue, session_factory, project_id):
    queue.register(
        "s",
        lambda job, session: (_ for _ in ()).throw(
            StageError("bad input", retryable=False)
        ),
    )
    with dbmod.session_scope(session_factory) as session:
        job_id = queue.enqueue(session, project_id, "s").id
    queue.run_pending()
    with session_factory() as session:
        job = session.get(PipelineJob, job_id)
        assert job.status == JobStatus.failed and job.attempts == 1


def test_missing_handler_fails_without_retry(queue, session_factory, project_id):
    with dbmod.session_scope(session_factory) as session:
        job_id = queue.enqueue(session, project_id, "nope").id
    queue.run_pending()
    with session_factory() as session:
        job = session.get(PipelineJob, job_id)
        assert job.status == JobStatus.failed and job.error_code == "no_handler"


def test_recover_stale_after_worker_death(queue, session_factory, project_id):
    with dbmod.session_scope(session_factory) as session:
        job = queue.enqueue(session, project_id, "research")
        claimed = queue.claim_next(session)
        assert claimed.id == job.id and claimed.status == JobStatus.running
        job_id = job.id
    # Simulate a crash: the worker never finished. Not stale yet...
    with dbmod.session_scope(session_factory) as session:
        assert queue.recover_stale(session) == 0
    # ...stale after the lock timeout.
    later = utcnow() + timedelta(seconds=120)
    with dbmod.session_scope(session_factory) as session:
        assert queue.recover_stale(session, now=later) == 1
    with session_factory() as session:
        job = session.get(PipelineJob, job_id)
        assert job.status == JobStatus.queued
        assert job.error_code == "worker_lost" and job.attempts == 1


def test_escalate_marks_needs_human(queue, session_factory, project_id):
    with dbmod.session_scope(session_factory) as session:
        job = queue.enqueue(session, project_id, "s")
        queue.escalate(session, job, "no sources found")
        assert job.status == JobStatus.needs_human
        assert job.error_message == "no sources found"


def test_mark_failed_error_info_roundtrip(queue, session_factory, project_id):
    info = ErrorInfo(code="x", message="m", module="mod")
    with dbmod.session_scope(session_factory) as session:
        job = queue.enqueue(session, project_id, "s")
        job.attempts = 3
        queue.mark_failed(session, job, info)
        assert job.status == JobStatus.failed
        assert job.error_at == info.occurred_at
