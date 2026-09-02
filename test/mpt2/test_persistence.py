"""State must survive a process restart: reopen the same file with a new engine."""

from __future__ import annotations

from mpt2 import db as dbmod
from mpt2 import services
from mpt2.jobs import JobQueue
from mpt2.models import PipelineJob, VideoProject
from mpt2.models.enums import JobStatus, ProjectState
from mpt2.state_machine import transition


def test_project_state_survives_restart(migrated_db, channel_config):
    engine = dbmod.make_engine(migrated_db)
    factory = dbmod.make_session_factory(engine)
    with dbmod.session_scope(factory) as session:
        channel = services.create_channel(session, channel_config)
        project = services.create_project(session, channel, title="T", topic="topic")
        transition(session, project, ProjectState.researching, reason="start")
        transition(session, project, ProjectState.failed, reason="crash")
        queue = JobQueue(factory, worker_id="w1")
        queue.enqueue(session, project.id, "research", {"topic": "topic"})
        project_id = project.id
    engine.dispose()  # "process exit"

    engine2 = dbmod.make_engine(migrated_db)
    factory2 = dbmod.make_session_factory(engine2)
    with factory2() as session:
        project = session.get(VideoProject, project_id)
        assert project.state == ProjectState.failed
        assert project.failed_from_state == "researching"
        assert [t.to_state for t in project.transitions] == ["researching", "failed"]
        jobs = project.jobs
        assert len(jobs) == 1 and jobs[0].status == JobStatus.queued
        # Resume exactly where it failed.
        transition(session, project, ProjectState.researching, reason="resume")
        session.commit()
    engine2.dispose()

    engine3 = dbmod.make_engine(migrated_db)
    with dbmod.make_session_factory(engine3)() as session:
        assert session.get(VideoProject, project_id).state == ProjectState.researching
        assert (
            session.get(
                PipelineJob, session.get(VideoProject, project_id).jobs[0].id
            ).status
            == JobStatus.queued
        )
    engine3.dispose()


def test_api_restart_keeps_data(settings, channel_config):
    from fastapi.testclient import TestClient

    from mpt2.api.app import create_app

    app1 = create_app(settings)
    with TestClient(app1) as client:
        channel = client.post(
            "/api/v2/channels", json=channel_config.model_dump()
        ).json()
        project = client.post(
            "/api/v2/projects",
            json={"channel_id": channel["id"], "title": "T", "topic": "topic"},
        ).json()
        client.post(
            f"/api/v2/projects/{project['id']}/transition",
            json={"to_state": "researching"},
        )
    app1.state.engine.dispose()

    app2 = create_app(settings)
    with TestClient(app2) as client:
        again = client.get(f"/api/v2/projects/{project['id']}").json()
        assert again["state"] == "researching"
        assert client.get("/api/v2/channels").json()[0]["slug"] == channel_config.slug
    app2.state.engine.dispose()
