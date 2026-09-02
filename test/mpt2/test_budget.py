from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from mpt2 import db as dbmod
from mpt2 import services
from mpt2.costs.guard import BudgetExceededError, BudgetGuard
from mpt2.errors import StageError
from mpt2.llm.backend import FakeBackend
from mpt2.llm.client import LLMClient
from mpt2.models import CostEntry, LLMCall, utcnow
from mpt2.models.enums import CostUnit
from test.mpt2.conftest import make_settings

# Make the fake backend expensive: 1 EUR per 1000 tokens in, 2 EUR per 1000 out.
PRICED = json.dumps(
    {"fake": {"input": 1_000_000.0 / 1000, "output": 2_000_000.0 / 1000}}
)


@pytest.fixture
def priced_settings(tmp_path):
    return make_settings(
        tmp_path,
        MPT2_LLM_PRICING_JSON=PRICED,
        MPT2_USD_TO_EUR="1",
        MPT2_BUDGET_PER_CALL_EUR="5",
        MPT2_BUDGET_PROJECT_EUR="12",
        MPT2_BUDGET_MONTHLY_HARD_EUR="20",
        MPT2_BUDGET_WARN_EUR="15",
    )


@pytest.fixture
def ids(session_factory, channel_config):
    with dbmod.session_scope(session_factory) as session:
        ch = services.create_channel(session, channel_config)
        p = services.create_project(session, ch, title="T", topic="t")
        p2 = services.create_project(session, ch, title="T2", topic="t")
        return {"channel": ch.id, "p1": p.id, "p2": p2.id}


def test_defaults_match_policy(settings, session):
    guard = BudgetGuard(settings)
    limits = guard.limits(session)
    assert limits == {
        "warn_eur": 100.0,
        "monthly_hard_eur": 1000.0,
        "project_eur": 30.0,
        "per_call_eur": 2.0,
    }


def test_admin_can_change_limits_but_never_remove_them(settings, session):
    guard = BudgetGuard(settings)
    merged = guard.set_limits(
        session,
        {"monthly_hard_eur": 2000, "project_eur": 60},
        actor="xavi",
        note="phase 2",
    )
    assert merged["monthly_hard_eur"] == 2000 and merged["project_eur"] == 60
    assert guard.limits(session)["project_eur"] == 60
    for bad in (
        {"project_eur": 0},
        {"per_call_eur": -1},
        {"warn_eur": None},
        {"unknown": 5},
    ):
        with pytest.raises(ValueError):
            guard.set_limits(session, bad, actor="xavi")
    with pytest.raises(ValueError, match="cannot exceed"):
        guard.set_limits(session, {"project_eur": 5000}, actor="xavi")


def test_per_call_limit_blocks_before_spend(priced_settings, session_factory, ids):
    backend = FakeBackend(lambda r: {"ok": True}, tokens_in=100, tokens_out=100)
    client = LLMClient(
        priced_settings, backend, session_factory, BudgetGuard(priced_settings)
    )
    # estimate: ~ (chars/3.2+200) tokens in * 1000 EUR/M + 4000 out * 2000 EUR/M = ~8.6 EUR > 5 EUR per call
    with pytest.raises(StageError) as exc:
        client.call(
            "dossier",
            "x" * 100,
            project_id=ids["p1"],
            channel_id=ids["channel"],
            max_tokens=4000,
        )
    assert exc.value.code == "budget_exceeded" and backend.calls == []
    with session_factory() as session:
        call = session.scalar(select(LLMCall))
        assert call.status.value == "blocked" and "per-call" in call.error_message
        assert session.scalar(select(CostEntry)) is None  # nothing spent


def test_project_and_monthly_limits(priced_settings, session_factory, ids):
    backend = FakeBackend(
        lambda r: {"ok": True}, tokens_in=1000, tokens_out=1000
    )  # 3 EUR per call
    client = LLMClient(
        priced_settings, backend, session_factory, BudgetGuard(priced_settings)
    )
    kwargs = dict(channel_id=ids["channel"], max_tokens=1000, use_cache=False)
    for (
        prompt
    ) in "abcd":  # 12 EUR spent on p1 (limit 12); next estimate ~2.2 EUR is blocked
        client.call("dossier", prompt, project_id=ids["p1"], **kwargs)
    with pytest.raises(StageError, match="project limit"):
        client.call("dossier", "e", project_id=ids["p1"], **kwargs)
    # other project still allowed until the monthly hard limit (20 EUR) bites
    client.call("dossier", "f", project_id=ids["p2"], **kwargs)  # month 15
    client.call("dossier", "g", project_id=ids["p2"], **kwargs)  # month 18
    with pytest.raises(StageError, match="monthly hard limit"):
        client.call("dossier", "h", project_id=ids["p2"], **kwargs)  # 18 + ~2.2 > 20
    with session_factory() as session:
        snap = BudgetGuard(priced_settings).snapshot(session, ids["p1"])
        assert snap.project_spent_eur == pytest.approx(12.0)
        assert snap.month_spent_eur == pytest.approx(18.0)
        assert snap.warning is True  # 18 >= warn 15
        assert len(backend.calls) == 6
        assert (
            session.scalar(select(__import__("sqlalchemy").func.count(CostEntry.id)))
            == 6
        )


def test_month_window_ignores_last_month(priced_settings, session_factory, ids):
    with dbmod.session_scope(session_factory) as session:
        old = services.record_cost(
            session,
            channel_id=ids["channel"],
            project_id=ids["p2"],
            provider="x",
            stage="s",
            units=1,
            unit_type=CostUnit.eur,
            est_cost_eur=500.0,
        )
        old.created_at = utcnow() - timedelta(days=45)
    with session_factory() as session:
        guard = BudgetGuard(priced_settings)
        assert guard.month_spent(session) == 0.0
        assert guard.project_spent(session, ids["p2"]) == 500.0
        with pytest.raises(BudgetExceededError):
            guard.check(session, estimated_eur=1.0, project_id=ids["p2"], what="t")
        guard.check(session, estimated_eur=1.0, project_id=ids["p1"], what="t")


def test_budget_api(client):
    body = client.get("/api/v2/budget").json()
    assert body["monthly_hard_eur"] == 1000.0 and body["warning"] is False
    ok = client.put("/api/v2/budget", json={"actor": "xavi", "project_eur": 40})
    assert ok.status_code == 200 and ok.json()["project_eur"] == 40
    assert client.get("/api/v2/budget").json()["project_eur"] == 40
    bad = client.put("/api/v2/budget", json={"actor": "xavi", "per_call_eur": 0})
    assert bad.status_code == 422
    assert client.put("/api/v2/budget", json={"actor": "xavi"}).status_code == 422
