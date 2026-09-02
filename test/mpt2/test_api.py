from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mpt2.api.app import create_app
from mpt2.settings import Settings
from test.mpt2.conftest import CHANNEL_PAYLOAD


def _channel(client: TestClient) -> dict:
    response = client.post("/api/v2/channels", json=CHANNEL_PAYLOAD)
    assert response.status_code == 201, response.text
    return response.json()


def _project(client: TestClient, channel_id: str) -> dict:
    response = client.post(
        "/api/v2/projects",
        json={
            "channel_id": channel_id,
            "title": "WeWork",
            "topic": "How WeWork lost billions",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_health(client):
    body = client.get("/api/v2/health").json()
    assert body["status"] == "ok"
    assert body["schema_current"] is True
    assert body["schema_revision"] == "0001"


def test_channel_crud(client):
    created = _channel(client)
    assert created["slug"] == "test-channel" and created["active"] is True
    assert client.get("/api/v2/channels").json()[0]["id"] == created["id"]
    assert (
        client.get(f"/api/v2/channels/{created['id']}").json()["name"] == "Test Channel"
    )
    assert client.get("/api/v2/channels/test-channel").json()["id"] == created["id"]
    assert client.get("/api/v2/channels/missing").status_code == 404


def test_channel_validation_error(client):
    response = client.post(
        "/api/v2/channels", json={**CHANNEL_PAYLOAD, "country": "USA"}
    )
    assert response.status_code == 422


def test_project_lifecycle_via_api(client):
    channel = _channel(client)
    project = _project(client, "test-channel")
    assert project["state"] == "idea" and project["language"] == "en-US"
    pid = project["id"]

    state = client.get(f"/api/v2/projects/{pid}/state").json()
    assert state["allowed_transitions"] == ["rejected", "researching"]
    assert state["history"] == []

    bad = client.post(
        f"/api/v2/projects/{pid}/transition", json={"to_state": "rendering"}
    )
    assert bad.status_code == 409
    assert bad.json()["error"]["code"] == "invalid_transition"

    for target in [
        "researching",
        "script_draft",
        "fact_check",
        "storyboard",
        "assets",
        "voice",
        "rendering",
        "quality_control",
        "awaiting_approval",
    ]:
        ok = client.post(
            f"/api/v2/projects/{pid}/transition",
            json={"to_state": target, "actor": "test"},
        )
        assert ok.status_code == 200, ok.text
    assert client.get(f"/api/v2/projects/{pid}").json()["state"] == "awaiting_approval"
    assert len(client.get(f"/api/v2/projects/{pid}/state").json()["history"]) == 9

    listed = client.get("/api/v2/projects", params={"channel_id": channel["id"]}).json()
    assert [p["id"] for p in listed] == [pid]

    unknown = client.post(
        f"/api/v2/projects/{pid}/transition", json={"to_state": "publishing"}
    )
    assert unknown.status_code == 422


def test_approval_moves_project_and_is_recorded(client):
    _channel(client)
    pid = _project(client, "test-channel")["id"]
    for target in [
        "researching",
        "script_draft",
        "fact_check",
        "storyboard",
        "assets",
        "voice",
        "rendering",
        "quality_control",
        "awaiting_approval",
    ]:
        client.post(f"/api/v2/projects/{pid}/transition", json={"to_state": target})

    early = client.post(
        f"/api/v2/projects/{pid}/approvals",
        json={
            "stage": "script",
            "decision": "approve",
            "reviewer": "xavi",
            "notes": "ok",
        },
    )
    assert early.status_code == 201 and early.json()["resulting_state"] is None

    final = client.post(
        f"/api/v2/projects/{pid}/approvals",
        json={"stage": "final", "decision": "approve", "reviewer": "xavi"},
    )
    assert final.status_code == 201
    assert final.json()["resulting_state"] == "approved"
    assert client.get(f"/api/v2/projects/{pid}").json()["state"] == "approved"
    approvals = client.get(f"/api/v2/projects/{pid}/approvals").json()
    assert [a["stage"] for a in approvals] == ["script", "final"]


def test_rejection_at_final(client):
    _channel(client)
    pid = _project(client, "test-channel")["id"]
    for target in [
        "researching",
        "script_draft",
        "fact_check",
        "storyboard",
        "assets",
        "voice",
        "rendering",
        "quality_control",
        "awaiting_approval",
    ]:
        client.post(f"/api/v2/projects/{pid}/transition", json={"to_state": target})
    response = client.post(
        f"/api/v2/projects/{pid}/approvals",
        json={
            "stage": "final",
            "decision": "reject",
            "reviewer": "xavi",
            "notes": "weak hook",
        },
    )
    assert response.json()["resulting_state"] == "rejected"
    state = client.get(f"/api/v2/projects/{pid}/state").json()
    assert state["state"] == "rejected"
    assert state["history"][-1]["actor"] == "human:xavi"
    assert state["history"][-1]["reason"] == "weak hook"


def test_costs_and_errors_endpoints(client, session_factory):
    _channel(client)
    pid = _project(client, "test-channel")["id"]
    assert client.get(f"/api/v2/projects/{pid}/costs").json() == {
        "project_id": pid,
        "total_est_cost_eur": 0.0,
        "entries": [],
    }
    for cost in (0.0, 0.25):
        response = client.post(
            f"/api/v2/projects/{pid}/costs",
            json={
                "provider": "ollama",
                "stage": "researching",
                "units": 1500,
                "unit_type": "tokens",
                "est_cost_eur": cost,
            },
        )
        assert response.status_code == 201
    summary = client.get(f"/api/v2/projects/{pid}/costs").json()
    assert summary["total_est_cost_eur"] == 0.25 and len(summary["entries"]) == 2

    assert client.get(f"/api/v2/projects/{pid}/errors").json() == []
    assert client.get(f"/api/v2/projects/{pid}/jobs").json() == []

    from mpt2 import db as dbmod
    from mpt2.errors import StageError
    from mpt2.jobs import JobQueue

    queue = JobQueue(session_factory, max_attempts=1, retry_base_seconds=0)
    queue.register(
        "research",
        lambda job, session: (_ for _ in ()).throw(
            StageError("no sources", code="no_sources")
        ),
    )
    with dbmod.session_scope(session_factory) as session:
        queue.enqueue(session, pid, "research")
    queue.run_pending()
    errors = client.get(f"/api/v2/projects/{pid}/errors").json()
    assert errors[0]["code"] == "no_sources" and errors[0]["status"] == "failed"
    jobs = client.get(f"/api/v2/projects/{pid}/jobs").json()
    assert jobs[0]["error_code"] == "no_sources"


def test_not_found_and_bad_channel(client):
    assert client.get("/api/v2/projects/nope").status_code == 404
    response = client.post(
        "/api/v2/projects", json={"channel_id": "missing", "title": "T", "topic": "t"}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_api_key_required_when_configured(tmp_path):
    settings = Settings.from_env(
        {
            "MPT2_ENV": "test",
            "MPT2_DB_PATH": str(tmp_path / "k.sqlite3"),
            "MPT2_STORAGE_DIR": str(tmp_path),
            "MPT2_API_KEY": "k1",
        },
        env_file=None,
    )
    app = create_app(settings)
    client = TestClient(app)
    assert client.get("/api/v2/health").status_code == 200  # health is public
    assert client.get("/api/v2/channels").status_code == 401
    assert (
        client.get("/api/v2/channels", headers={"x-api-key": "wrong"}).status_code
        == 401
    )
    assert (
        client.get("/api/v2/channels", headers={"x-api-key": "k1"}).status_code == 200
    )
    app.state.engine.dispose()


@pytest.mark.parametrize("path", ["/api/v2/openapi.json", "/openapi.json"])
def test_openapi_lists_v2_routes(client, path):
    response = client.get(path)
    if response.status_code == 404:
        pytest.skip("openapi not served at this path")
    paths = response.json()["paths"]
    assert "/api/v2/projects/{project_id}/transition" in paths
