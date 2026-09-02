"""End-to-end editorial pipeline with the scripted fake backend."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from mpt2 import db as dbmod
from mpt2 import services
from mpt2.api.app import create_app
from mpt2.editorial.artifacts import ARTIFACT_FILES
from mpt2.jobs import JobQueue
from mpt2.llm.backend import FakeBackend, LLMBackendError
from mpt2.llm.fake_editorial import EditorialScripter
from mpt2.models import (
    LLMCall,
    PipelineJob,
    ResearchClaim,
    ResearchSource,
    ScriptSection,
)
from mpt2.models.enums import ProjectState
from mpt2.pipeline.runner import Pipeline, build_context
from mpt2.pipeline.stages import STAGE_NAMES
from mpt2.pipeline.worker import Worker
from mpt2.research.providers import FakeResearchProvider
from test.mpt2.conftest import CHANNEL_PAYLOAD, make_settings

CHANNEL = {
    **CHANNEL_PAYLOAD,
    "allowed_sources": ["regulator.example.test"],
    "banned_sources": ["tabloid.example.test"],
    "quality_threshold": 60.0,
}
PROJECT = {
    "channel_id": "test-channel",
    "title": "Pilot title",
    "topic": "Where the money is really made",
    "notes": json.dumps({"required_topics": ["margins", "financing"]}),
}


def _stack(settings, session_factory, scripter=None, research=None):
    backend = FakeBackend(scripter or EditorialScripter())
    ctx = build_context(
        settings,
        session_factory,
        backend=backend,
        research=research or FakeResearchProvider(),
    )
    queue = JobQueue(
        session_factory, max_attempts=settings.job_max_attempts, retry_base_seconds=0
    )
    return backend, Pipeline(ctx, queue), Worker(queue, session_factory)


def _project(session_factory):
    with dbmod.session_scope(session_factory) as session:
        ch = services.create_channel(
            session,
            __import__(
                "mpt2.channels.schema", fromlist=["ChannelConfig"]
            ).ChannelConfig(**CHANNEL),
        )
        p = services.create_project(
            session,
            ch,
            title=PROJECT["title"],
            topic=PROJECT["topic"],
            notes=PROJECT["notes"],
        )
        return p.id


@pytest.fixture
def e2e_client(settings):
    app = create_app(
        settings,
        backend=FakeBackend(EditorialScripter()),
        research_provider=FakeResearchProvider(),
    )
    with TestClient(app) as client:
        client.post("/api/v2/channels", json=CHANNEL)
        yield client
    app.state.engine.dispose()


def test_full_pipeline_reaches_editorial_review(e2e_client, settings):
    client = e2e_client
    pid = client.post("/api/v2/projects", json=PROJECT).json()["id"]
    run = client.post(f"/api/v2/projects/{pid}/run", json={"run_worker": True}).json()
    assert run["state"] == "editorial_review", client.get(
        f"/api/v2/projects/{pid}/errors"
    ).json()
    jobs = client.get(f"/api/v2/projects/{pid}/jobs").json()
    assert [j["stage"] for j in jobs] == STAGE_NAMES and all(
        j["status"] == "done" for j in jobs
    )
    history = [
        t["to_state"]
        for t in client.get(f"/api/v2/projects/{pid}/state").json()["history"]
    ]
    assert history == [
        "researching",
        "script_draft",
        "fact_check",
        "storyboard",
        "editorial_review",
    ]

    plan = client.get(f"/api/v2/projects/{pid}/research-plan").json()["plan"]
    assert len(plan["search_strategy"]) >= 4 and plan["hypotheses_to_test"]
    log = client.get(f"/api/v2/projects/{pid}/search-log").json()
    assert len(log) == len(plan["search_strategy"]) and all(
        q["status"] == "ok" for q in log
    )
    sources = client.get(f"/api/v2/projects/{pid}/sources").json()
    accepted = [s for s in sources if s["status"] == "accepted"]
    assert accepted and all(s["domain"] != "tabloid.example.test" for s in accepted)
    assert any(s["primary"] for s in accepted) and all(s["snippets"] for s in accepted)
    assert all("utm_" not in s["url"] for s in sources)  # canonical urls
    dossier = client.get(f"/api/v2/projects/{pid}/dossier").json()["dossier"]
    assert (
        dossier["executive_summary"]
        and dossier["contradictions"]
        and dossier["title_assessment"]
    )
    claims = client.get(f"/api/v2/projects/{pid}/claims").json()
    assert len(claims) >= 8
    unsupported = [c for c in claims if c["status"] == "unsupported"]
    assert (
        unsupported and "99" in unsupported[0]["text"]
    )  # numeric claim without evidence stays unsupported
    options = client.get(f"/api/v2/projects/{pid}/options").json()
    kinds = {
        o["kind"]: sum(1 for x in options if x["kind"] == o["kind"]) for o in options
    }
    assert kinds == {"angle": 5, "hook": 10, "structure": 3}
    assert sum(1 for o in options if o["selected"]) == 3 and all(
        o["selected_by"] == "system" for o in options if o["selected"]
    )
    script = client.get(f"/api/v2/projects/{pid}/script").json()
    assert 6 * 60 <= script["est_duration_s"] <= 8.6 * 60
    assert (
        script["sections"][0]["role"] == "hook"
        and script["sections"][-1]["role"] == "cta"
    )
    assert not any(s["needs_verification"] for s in script["sections"])
    sb = client.get(f"/api/v2/projects/{pid}/storyboard").json()
    assert len(sb["scenes"]) > 40 and sb["scenes"][0]["position"] == 0
    assert {s["section_id"] for s in sb["scenes"]} == {
        s["id"] for s in script["sections"]
    }
    assert any(s["chart_data"] for s in sb["scenes"]) and all(
        2 <= s["est_duration_s"] <= 30
        for s in sb["scenes"]
        if len(s["narration"].split()) > 4
    )
    quality = {
        q["kind"]: q for q in client.get(f"/api/v2/projects/{pid}/quality").json()
    }
    assert quality["fact_check"]["passed"] and quality["editorial"]["passed"]
    kinds_qc = {c["kind"] for c in quality["editorial"]["checks"]}
    assert kinds_qc == {"automatic", "llm"}
    report = client.get(f"/api/v2/projects/{pid}/cost-report").json()
    assert (
        report["llm_calls"] >= 10
        and "research_plan" in report["by_task"]
        and report["total_cost_eur"] == 0.0
    )
    assert client.get(f"/api/v2/projects/{pid}/review").json()["blockers"] == []


def test_traceability_claim_source_script_scene(settings, session_factory):
    pid = _project(session_factory)
    backend, pipeline, worker = _stack(settings, session_factory)
    with dbmod.session_scope(session_factory) as session:
        pipeline.start(session, services.get_project(session, pid))
    worker.run_until_idle()
    with session_factory() as session:
        project = services.get_project(session, pid)
        assert project.state == ProjectState.editorial_review
        sections = session.scalars(select(ScriptSection)).all()
        used = [s for s in sections if s.claim_ids]
        assert used
        source_ids = {
            s.id
            for s in session.scalars(
                select(ResearchSource).where(ResearchSource.project_id == pid)
            ).all()
        }
        for sec in used:
            for cid in sec.claim_ids:
                claim = session.get(ResearchClaim, cid)
                assert claim is not None and claim.used_in_script
                assert claim.sources and all(
                    src.id in source_ids for src in claim.sources
                )
                assert all(ev["source_id"] in source_ids for ev in claim.evidence)
        from mpt2.editorial.storyboard import current_storyboard

        scenes = current_storyboard(session, project).scenes
        linked = [s for s in scenes if s.claim_ids]
        assert linked
        for scene in linked:
            section = session.get(ScriptSection, scene.section_id)
            assert set(scene.claim_ids) <= set(section.claim_ids)
            assert scene.source_ids and set(scene.source_ids) <= source_ids


def test_artifacts_exported(settings, session_factory):
    pid = _project(session_factory)
    backend, pipeline, worker = _stack(settings, session_factory)
    with dbmod.session_scope(session_factory) as session:
        pipeline.start(session, services.get_project(session, pid))
    worker.run_until_idle()
    out = Path(settings.storage_dir) / "projects" / pid
    assert sorted(p.name for p in out.iterdir()) == sorted(ARTIFACT_FILES)
    research = json.loads((out / "research.json").read_text())
    assert research["dossier"]["key_facts"] and research["sources"]
    claims = json.loads((out / "claims.json").read_text())
    assert all(
        set(c)
        >= {
            "id",
            "text",
            "kind",
            "importance",
            "confidence",
            "status",
            "supporting_source_ids",
            "contradicting_source_ids",
            "evidence",
            "geographic_scope",
            "time_period",
            "used_in_script",
        }
        for c in claims
    )
    script = json.loads((out / "script.json").read_text())
    assert script["sections"] and all("source_ids" in s for s in script["sections"])
    assert (out / "script.md").read_text().startswith("# ")
    assert "## Sources" in (out / "research.md").read_text()
    cost = json.loads((out / "cost-report.json").read_text())
    assert cost["llm_calls"] > 0 and "by_task" in cost
    sb = json.loads((out / "storyboard.json").read_text())
    assert all(
        set(s)
        >= {
            "id",
            "position",
            "section_id",
            "narration",
            "est_duration_s",
            "narrative_goal",
            "visual_description",
            "asset_type",
            "search_terms",
            "on_screen_text",
            "chart_data",
            "motion",
            "transition",
            "claim_ids",
            "source_ids",
            "copyright_risk",
            "fallback_visual",
            "priority",
        }
        for s in sb["scenes"]
    )


def test_idempotent_rerun_makes_no_new_llm_calls(settings, session_factory):
    pid = _project(session_factory)
    backend, pipeline, worker = _stack(settings, session_factory)
    with dbmod.session_scope(session_factory) as session:
        pipeline.start(session, services.get_project(session, pid))
    worker.run_until_idle()
    calls_before = len(backend.calls)
    # Re-run every stage handler directly: artifacts exist, so nothing is recomputed.
    with dbmod.session_scope(session_factory) as session:
        project = services.get_project(session, pid)
        for job in session.scalars(
            select(PipelineJob).where(PipelineJob.project_id == pid)
        ).all():
            stage = __import__(
                "mpt2.pipeline.stages", fromlist=["STAGE_BY_NAME"]
            ).STAGE_BY_NAME[job.stage]
            project.state = stage.state
            result = stage.handler(pipeline.ctx, job, session)
            assert result.get("skipped") is True, job.stage
        project.state = ProjectState.editorial_review
    assert len(backend.calls) == calls_before
    # Enqueueing the same stage again returns the existing job (idempotency key).
    with dbmod.session_scope(session_factory) as session:
        project = services.get_project(session, pid)
        jobs_before = session.scalar(
            select(__import__("sqlalchemy").func.count(PipelineJob.id))
        )
        pipeline.queue.enqueue(
            session,
            pid,
            "research_plan",
            {"run": 1},
            idempotency_key=f"{pid}:research_plan:run1",
        )
        assert (
            session.scalar(select(__import__("sqlalchemy").func.count(PipelineJob.id)))
            == jobs_before
        )


def test_resume_after_crash_mid_pipeline(settings, session_factory):
    """The process dies while the dossier stage runs; a new worker resumes without redoing research."""
    pid = _project(session_factory)
    crash = {"armed": True}

    def scripter(request):
        if request.metadata.get("task") == "dossier" and crash["armed"]:
            crash["armed"] = False
            raise LLMBackendError(
                "connection reset", code="llm_connection_error", retryable=True
            )
        return EditorialScripter()(request)

    backend, pipeline, worker = _stack(settings, session_factory, scripter=scripter)
    with dbmod.session_scope(session_factory) as session:
        pipeline.start(session, services.get_project(session, pid))
    worker.run_until_idle()
    with session_factory() as session:
        project = services.get_project(session, pid)
        assert (
            project.state == ProjectState.editorial_review
        )  # bounded retry recovered it
        errors = services.project_errors(session, pid)
        assert (
            not errors
            or errors[0]["code"] != "llm_connection_error"
            or errors[0]["status"] == "done"
        )
        error_calls = session.scalars(
            select(LLMCall).where(LLMCall.status == "error")
        ).all()
        assert (
            len(error_calls) == 1
            and error_calls[0].error_code == "llm_connection_error"
        )

    # Now a hard crash: simulate a worker that claimed a job and died, then restart.
    pid2 = _project(session_factory)
    backend2, pipeline2, worker2 = _stack(settings, session_factory)
    with dbmod.session_scope(session_factory) as session:
        pipeline2.start(session, services.get_project(session, pid2))
    worker2.queue.run_pending(max_jobs=2)  # plan + search done
    with dbmod.session_scope(session_factory) as session:
        job = pipeline2.queue.claim_next(session)  # dossier claimed, never finished
        assert job.stage == "research_dossier"
        job.locked_at = job.locked_at.replace(year=2000)
        searches_done = session.scalar(
            select(__import__("sqlalchemy").func.count(ResearchSource.id)).where(
                ResearchSource.project_id == pid2
            )
        )
    # "restart": brand-new pipeline + worker instances over the same database
    backend3, pipeline3, worker3 = _stack(settings, session_factory)
    worker3.run_until_idle()
    with session_factory() as session:
        project = services.get_project(session, pid2)
        assert project.state == ProjectState.editorial_review
        assert (
            session.scalar(
                select(__import__("sqlalchemy").func.count(ResearchSource.id)).where(
                    ResearchSource.project_id == pid2
                )
            )
            == searches_done
        )
        assert not any(
            r.metadata.get("task") in ("research_plan",) for r in backend3.calls
        )  # research not redone


def test_failed_project_resumes_at_failed_stage(settings, session_factory):
    pid = _project(session_factory)
    fail = {"on": True}

    def scripter(request):
        if request.metadata.get("task") == "angles_hooks" and fail["on"]:
            raise LLMBackendError("auth", code="llm_auth_error", retryable=False)
        return EditorialScripter()(request)

    backend, pipeline, worker = _stack(settings, session_factory, scripter=scripter)
    with dbmod.session_scope(session_factory) as session:
        pipeline.start(session, services.get_project(session, pid))
    worker.run_until_idle()
    with session_factory() as session:
        project = services.get_project(session, pid)
        assert (
            project.state == ProjectState.failed
            and project.failed_from_state == "script_draft"
        )
        errors = services.project_errors(session, pid)
        assert (
            errors[0]["code"] == "llm_auth_error"
            and errors[0]["stage"] == "angles_hooks"
        )
        assert session.scalars(
            select(LLMCall).where(LLMCall.status == "error")
        ).all()  # telemetry survived the rollback
    fail["on"] = False
    with dbmod.session_scope(session_factory) as session:
        pipeline.resume(
            session,
            services.get_project(session, pid),
            actor="xavi",
            reason="key fixed",
        )
    worker.run_until_idle()
    with session_factory() as session:
        assert services.get_project(session, pid).state == ProjectState.editorial_review


def test_insufficient_sources_never_fabricates(settings, session_factory):
    pid = _project(session_factory)
    provider = FakeResearchProvider(search_fn=lambda q, ctx: [])
    backend, pipeline, worker = _stack(settings, session_factory, research=provider)
    with dbmod.session_scope(session_factory) as session:
        pipeline.start(session, services.get_project(session, pid))
    worker.run_until_idle()
    with session_factory() as session:
        project = services.get_project(session, pid)
        assert project.state == ProjectState.failed
        errors = services.project_errors(session, pid)
        assert errors[0]["code"] == "insufficient_sources"
        assert (
            session.scalar(
                select(__import__("sqlalchemy").func.count(ResearchSource.id))
            )
            == 0
        )
        assert not any(r.metadata.get("task") == "dossier" for r in backend.calls)


def test_approval_blocked_by_critical_unsupported_claim(e2e_client):
    client = e2e_client
    pid = client.post("/api/v2/projects", json=PROJECT).json()["id"]
    client.post(f"/api/v2/projects/{pid}/run", json={"run_worker": True})
    script = client.get(f"/api/v2/projects/{pid}/script").json()
    used = next(s for s in script["sections"] if s["claim_ids"])
    cid = used["claim_ids"][0]
    # A human marks the claim critical + unsupported: approval must be blocked.
    patched = client.patch(
        f"/api/v2/claims/{cid}",
        json={
            "actor": "xavi",
            "reason": "source retracted",
            "importance": "critical",
            "verification_status": "unsupported",
        },
    )
    assert patched.status_code == 200 and patched.json()["human_edited"] is True
    blockers = client.get(f"/api/v2/projects/{pid}/review").json()["blockers"]
    assert any("critical claim" in b for b in blockers) and any(
        "rerun script_fact_check" in b for b in blockers
    )
    denied = client.post(
        f"/api/v2/projects/{pid}/review",
        json={"stage": "package", "decision": "approve", "reviewer": "xavi"},
    )
    assert (
        denied.status_code == 409
        and "cannot be approved" in denied.json()["error"]["message"]
    )
    assert client.get(f"/api/v2/projects/{pid}").json()["state"] == "editorial_review"
    # Discarding the claim and rerunning the fact-check clears the blocker if the section no longer uses it.
    client.patch(f"/api/v2/claims/{cid}", json={"actor": "xavi", "discarded": True})
    client.patch(
        f"/api/v2/sections/{used['id']}",
        json={
            "actor": "xavi",
            "reason": "drop unsourced figure",
            "text": "A short replacement sentence without numbers.",
            "claim_ids": [],
        },
    )
    rerun = client.post(
        f"/api/v2/projects/{pid}/rerun",
        json={"stage": "script_fact_check", "actor": "xavi", "run_worker": True},
    )
    assert rerun.status_code == 200, rerun.text
    assert rerun.json()["state"] == "editorial_review" and rerun.json()["run"] == 2
    events = client.get(f"/api/v2/projects/{pid}/review").json()["events"]
    assert [e["action"] for e in events][:3] == ["edit", "discard", "edit"] and events[
        0
    ]["actor"] == "xavi"


def test_human_selection_and_package_decisions(e2e_client, settings):
    client = e2e_client
    pid = client.post("/api/v2/projects", json=PROJECT).json()["id"]
    client.post(f"/api/v2/projects/{pid}/run", json={"run_worker": True})
    hooks = [
        o
        for o in client.get(f"/api/v2/projects/{pid}/options").json()
        if o["kind"] == "hook"
    ]
    other = next(h for h in hooks if not h["selected"])
    sel = client.post(
        f"/api/v2/options/{other['id']}/select",
        json={"actor": "xavi", "reason": "sharper"},
    )
    assert sel.json()["selected_by"] == "human:xavi"
    hooks = [
        o
        for o in client.get(f"/api/v2/projects/{pid}/options").json()
        if o["kind"] == "hook"
    ]
    assert [h["id"] for h in hooks if h["selected"]] == [other["id"]]
    # Rerun the script with the human hook; selection survives the run bump.
    rerun = client.post(
        f"/api/v2/projects/{pid}/rerun",
        json={"stage": "script_write", "actor": "xavi", "run_worker": True},
    ).json()
    assert rerun["state"] == "editorial_review" and rerun["run"] == 2
    script = client.get(f"/api/v2/projects/{pid}/script").json()
    assert script["hook_id"] == other["id"]
    # research approval is recorded only; package approval moves to assets.
    r = client.post(
        f"/api/v2/projects/{pid}/review",
        json={
            "stage": "research",
            "decision": "approve",
            "reviewer": "xavi",
            "notes": "solid",
        },
    )
    assert r.status_code == 201 and r.json()["resulting_state"] is None
    scene = client.get(f"/api/v2/projects/{pid}/storyboard").json()["scenes"][0]
    edited = client.patch(
        f"/api/v2/scenes/{scene['id']}",
        json={
            "actor": "xavi",
            "visual_description": "Archive photo of a 1990s showroom",
            "asset_type": "photo",
        },
    )
    assert edited.status_code == 200 and edited.json()["human_edited"] is True
    approved = client.post(
        f"/api/v2/projects/{pid}/review",
        json={
            "stage": "package",
            "decision": "approve",
            "reviewer": "xavi",
            "notes": "go",
        },
    )
    assert approved.json()["resulting_state"] == "assets"
    assert client.get(f"/api/v2/projects/{pid}").json()["state"] == "assets"
    again = client.post(
        f"/api/v2/projects/{pid}/review",
        json={"stage": "package", "decision": "approve", "reviewer": "xavi"},
    )
    assert again.status_code == 409


def test_package_reject(e2e_client):
    client = e2e_client
    pid = client.post("/api/v2/projects", json=PROJECT).json()["id"]
    client.post(f"/api/v2/projects/{pid}/run", json={"run_worker": True})
    r = client.post(
        f"/api/v2/projects/{pid}/review",
        json={
            "stage": "package",
            "decision": "reject",
            "reviewer": "xavi",
            "notes": "not original enough",
        },
    )
    assert r.json()["resulting_state"] == "rejected"
    history = client.get(f"/api/v2/projects/{pid}/state").json()["history"]
    assert (
        history[-1]["actor"] == "human:xavi"
        and history[-1]["reason"] == "not original enough"
    )


def test_budget_block_escalates_to_human(tmp_path, session_factory):
    settings = make_settings(
        tmp_path,
        MPT2_LLM_PRICING_JSON=json.dumps(
            {"fake": {"input": 1_000_000.0, "output": 1_000_000.0}}
        ),
        MPT2_USD_TO_EUR="1",
        MPT2_BUDGET_PER_CALL_EUR="1",
        MPT2_BUDGET_PROJECT_EUR="1",
        MPT2_BUDGET_MONTHLY_HARD_EUR="1",
    )
    pid = _project(session_factory)
    backend, pipeline, worker = _stack(settings, session_factory)
    with dbmod.session_scope(session_factory) as session:
        pipeline.start(session, services.get_project(session, pid))
    worker.run_until_idle()
    with session_factory() as session:
        project = services.get_project(session, pid)
        assert project.state == ProjectState.failed
        errors = services.project_errors(session, pid)
        assert errors[0]["code"] == "budget_exceeded" and errors[0]["attempts"] == 1
        assert backend.calls == []  # blocked before any spend
        blocked = session.scalars(
            select(LLMCall).where(LLMCall.status == "blocked")
        ).all()
        assert blocked and blocked[0].task == "research_plan"


def test_cli_project_flow(tmp_path, monkeypatch, capsys):
    from mpt2.__main__ import main

    env = {
        "MPT2_ENV": "test",
        "MPT2_DB_PATH": str(tmp_path / "cli.sqlite3"),
        "MPT2_STORAGE_DIR": str(tmp_path / "st"),
        "MPT2_LLM_BACKEND": "fake",
        "MPT2_JOB_RETRY_BASE_SECONDS": "0",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert main(["--no-env-file", "migrate"]) == 0
    assert (
        main(
            [
                "--no-env-file",
                "import-channel",
                str(
                    Path(__file__).resolve().parents[2]
                    / "channels"
                    / "business-stories-en.yaml"
                ),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "--no-env-file",
                "project",
                "create",
                "--channel",
                "business-stories-en",
                "--title",
                "Pilot",
                "--required-topic",
                "margins",
            ]
        )
        == 0
    )
    pid = json.loads(capsys.readouterr().out)["id"]
    assert main(["--no-env-file", "project", "run", pid]) == 0
    out = capsys.readouterr().out
    assert '"state": "editorial_review"' in out
    assert main(["--no-env-file", "project", "status", pid]) == 0
    status = json.loads(capsys.readouterr().out)
    assert (
        status["state"] == "editorial_review"
        and status["blockers"] == []
        and status["llm_calls"] > 0
    )
    assert main(["--no-env-file", "project", "export", pid]) == 0
    assert set(json.loads(capsys.readouterr().out)["files"]) == set(ARTIFACT_FILES)
    assert (
        main(
            ["--no-env-file", "budget", "set", "--actor", "xavi", "--project-eur", "45"]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["project_eur"] == 45
    assert (
        main(
            [
                "--no-env-file",
                "project",
                "review",
                pid,
                "--stage",
                "package",
                "--decision",
                "approve",
                "--reviewer",
                "xavi",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["state"] == "assets"
    assert main(["--no-env-file", "worker", "--once"]) == 0


def test_script_repair_path_removes_unsourced_numbers(e2e_client):
    client = e2e_client
    pid = client.post("/api/v2/projects", json=PROJECT).json()["id"]
    client.post(f"/api/v2/projects/{pid}/run", json={"run_worker": True})
    script = client.get(f"/api/v2/projects/{pid}/script").json()
    target = script["sections"][3]
    # A human introduces an unsourced figure; the fact-check must repair or flag it.
    client.patch(
        f"/api/v2/sections/{target['id']}",
        json={
            "actor": "xavi",
            "text": "Dealers pocketed 4,567 dollars per car last year.",
            "claim_ids": [],
        },
    )
    assert any(
        "rerun script_fact_check" in b
        for b in client.get(f"/api/v2/projects/{pid}/review").json()["blockers"]
    )
    rerun = client.post(
        f"/api/v2/projects/{pid}/rerun",
        json={"stage": "script_fact_check", "actor": "xavi", "run_worker": True},
    )
    assert rerun.status_code == 200 and rerun.json()["state"] == "editorial_review"
    tasks = [c["task"] for c in client.get(f"/api/v2/projects/{pid}/llm-calls").json()]
    assert "script_repair" in tasks
    section = next(
        s
        for s in client.get(f"/api/v2/projects/{pid}/script").json()["sections"]
        if s["id"] == target["id"]
    )
    assert "4,567" not in section["text"] and section["needs_verification"] is False
    quality = {
        q["kind"]: q for q in client.get(f"/api/v2/projects/{pid}/quality").json()
    }
    assert quality["fact_check"]["passed"] is True
    assert client.get(f"/api/v2/projects/{pid}/review").json()["blockers"] == []


def test_worker_loop_runs_and_stops(settings, session_factory):
    pid = _project(session_factory)
    backend, pipeline, worker = _stack(settings, session_factory)
    with dbmod.session_scope(session_factory) as session:
        pipeline.start(session, services.get_project(session, pid))
    slept = []
    worker._sleep = slept.append
    worker.poll_seconds = 0.01
    total = worker.run_forever(max_iterations=3)
    assert total == len(STAGE_NAMES)
    with session_factory() as session:
        assert services.get_project(session, pid).state == ProjectState.editorial_review
    assert slept  # idle iterations sleep instead of spinning
