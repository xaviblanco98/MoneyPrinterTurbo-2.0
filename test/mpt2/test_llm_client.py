from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from mpt2 import db as dbmod
from mpt2 import services
from mpt2.costs.guard import BudgetGuard
from mpt2.errors import StageError
from mpt2.llm.backend import BackendRequest, FakeBackend, LLMBackendError
from mpt2.llm.client import DEFAULT_TASK_TIERS, LLMClient, extract_json
from mpt2.llm.pricing import PriceBook, PricingUnknownError
from mpt2.models import CostEntry, LLMCache, LLMCall
from pydantic import BaseModel
from test.mpt2.conftest import make_settings


class Answer(BaseModel):
    value: int
    label: str


@pytest.fixture
def project(session_factory, channel_config):
    with dbmod.session_scope(session_factory) as session:
        channel = services.create_channel(session, channel_config)
        p = services.create_project(session, channel, title="T", topic="topic")
        return {"project_id": p.id, "channel_id": channel.id}


def _client(settings, session_factory, scripter, **kw):
    backend = FakeBackend(scripter)
    return LLMClient(
        settings,
        backend,
        session_factory,
        BudgetGuard(settings),
        sleep=lambda s: None,
        **kw,
    ), backend


def test_task_routing_uses_settings(tmp_path):
    settings = make_settings(
        tmp_path,
        MPT2_LLM_BACKEND="anthropic",
        ANTHROPIC_API_KEY="k",
        MPT2_MODEL_FAST="claude-haiku-4-5",
        MPT2_MODEL_SMART="claude-sonnet-5",
        MPT2_LLM_TASK_TIERS=json.dumps({"storyboard": "smart"}),
        MPT2_LLM_TASK_MODELS=json.dumps({"editorial_qc": "claude-opus-5"}),
    )

    class Dummy:
        name = "anthropic"

        def complete(self, request):
            raise AssertionError

    client = LLMClient(settings, Dummy(), lambda: None)
    assert client.model_for("claim_extraction") == "claude-haiku-4-5"  # fast tier
    assert client.model_for("script_write") == "claude-sonnet-5"  # smart tier
    assert client.model_for("storyboard") == "claude-sonnet-5"  # overridden tier
    assert client.model_for("editorial_qc") == "claude-opus-5"  # explicit model
    assert set(DEFAULT_TASK_TIERS.values()) == {"fast", "smart"}


def test_structured_call_records_telemetry_and_cost(settings, session_factory, project):
    client, backend = _client(
        settings, session_factory, lambda r: {"value": 7, "label": "ok"}
    )
    result = client.call(
        "claim_extraction",
        "Extract",
        schema=Answer,
        project_id=project["project_id"],
        channel_id=project["channel_id"],
        stage="research",
    )
    assert result.parsed == Answer(value=7, label="ok")
    assert result.cache_hit is False and result.model == "fake"
    with session_factory() as session:
        call = session.scalar(select(LLMCall))
        assert call.task == "claim_extraction" and call.status.value == "ok"
        assert (
            call.tokens_in == 500 and call.tokens_out == 300 and call.prompt_chars > 0
        )
        assert len(call.prompt_sha256) == 64
        assert session.scalar(select(CostEntry)).provider == "fake:fake"


def test_cache_hit_avoids_second_backend_call(settings, session_factory, project):
    client, backend = _client(
        settings, session_factory, lambda r: {"value": 1, "label": "a"}
    )
    first = client.call(
        "dossier",
        "same prompt",
        schema=Answer,
        project_id=project["project_id"],
        channel_id=project["channel_id"],
    )
    second = client.call(
        "dossier",
        "same prompt",
        schema=Answer,
        project_id=project["project_id"],
        channel_id=project["channel_id"],
    )
    assert len(backend.calls) == 1
    assert (
        second.cache_hit is True
        and second.parsed == first.parsed
        and second.cost_eur == 0.0
    )
    third = client.call(
        "dossier",
        "different prompt",
        schema=Answer,
        project_id=project["project_id"],
        channel_id=project["channel_id"],
    )
    assert len(backend.calls) == 2 and third.cache_hit is False
    with session_factory() as session:
        assert session.scalar(select(LLMCache)).hits == 1
        assert [
            c.cache_hit
            for c in session.scalars(select(LLMCall).order_by(LLMCall.created_at)).all()
        ] == [False, True, False]


def test_cache_can_be_disabled(tmp_path, session_factory, project):
    settings = make_settings(tmp_path, MPT2_LLM_CACHE_ENABLED="false")
    client, backend = _client(
        settings, session_factory, lambda r: {"value": 1, "label": "a"}
    )
    for _ in range(2):
        client.call(
            "dossier",
            "p",
            schema=Answer,
            project_id=project["project_id"],
            channel_id=project["channel_id"],
        )
    assert len(backend.calls) == 2


def test_invalid_output_is_repaired_once(settings, session_factory, project):
    answers = iter(["not json", {"value": 3, "label": "fixed"}])
    client, backend = _client(settings, session_factory, lambda r: next(answers))
    result = client.call(
        "fact_check",
        "p",
        schema=Answer,
        project_id=project["project_id"],
        channel_id=project["channel_id"],
    )
    assert result.parsed.value == 3
    assert len(backend.calls) == 2 and backend.calls[1].metadata.get("repair") is True
    assert "Validation error" in backend.calls[1].prompt


def test_invalid_output_twice_fails_with_code(settings, session_factory, project):
    client, backend = _client(
        settings, session_factory, lambda r: {"value": "not-an-int"}
    )
    with pytest.raises(StageError) as exc:
        client.call(
            "fact_check",
            "p",
            schema=Answer,
            project_id=project["project_id"],
            channel_id=project["channel_id"],
        )
    assert exc.value.code == "llm_invalid_output" and len(backend.calls) == 2


def test_retry_on_retryable_backend_error_then_success(
    settings, session_factory, project
):
    attempts = {"n": 0}

    def flaky(request):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return LLMBackendError(
                "rate limited", code="llm_rate_limited", retryable=True
            )
        return {"value": 1, "label": "ok"}

    client, backend = _client(settings, session_factory, flaky)
    result = client.call(
        "dossier",
        "p",
        schema=Answer,
        project_id=project["project_id"],
        channel_id=project["channel_id"],
    )
    assert result.parsed.value == 1 and attempts["n"] == 3
    with session_factory() as session:
        statuses = [
            c.status.value
            for c in session.scalars(select(LLMCall).order_by(LLMCall.created_at)).all()
        ]
        assert statuses == ["error", "error", "ok"]


def test_retries_are_bounded(tmp_path, session_factory, project):
    settings = make_settings(tmp_path, MPT2_LLM_MAX_RETRIES="2")
    client, backend = _client(
        settings,
        session_factory,
        lambda r: LLMBackendError(
            "overloaded", code="llm_server_error", retryable=True
        ),
    )
    with pytest.raises(StageError) as exc:
        client.call(
            "dossier",
            "p",
            project_id=project["project_id"],
            channel_id=project["channel_id"],
        )
    assert exc.value.code == "llm_server_error" and len(backend.calls) == 3


def test_non_retryable_error_fails_immediately(settings, session_factory, project):
    client, backend = _client(
        settings,
        session_factory,
        lambda r: LLMBackendError("bad key", code="llm_auth_error", retryable=False),
    )
    with pytest.raises(StageError) as exc:
        client.call(
            "dossier",
            "p",
            project_id=project["project_id"],
            channel_id=project["channel_id"],
        )
    assert (
        exc.value.code == "llm_auth_error"
        and exc.value.retryable is False
        and len(backend.calls) == 1
    )


def test_prompt_with_secret_is_refused(tmp_path, session_factory, project):
    settings = make_settings(tmp_path, ANTHROPIC_API_KEY="sk-ant-verysecret123")
    client, backend = _client(
        settings, session_factory, lambda r: {"value": 1, "label": "a"}
    )
    with pytest.raises(StageError) as exc:
        client.call(
            "dossier",
            "please use sk-ant-verysecret123",
            project_id=project["project_id"],
            channel_id=project["channel_id"],
        )
    assert exc.value.code == "prompt_contains_secret" and backend.calls == []


def test_prompts_are_never_persisted(settings, session_factory, project):
    client, backend = _client(
        settings, session_factory, lambda r: {"value": 1, "label": "a"}
    )
    client.call(
        "dossier",
        "UNIQUE-PROMPT-TEXT-XYZ",
        project_id=project["project_id"],
        channel_id=project["channel_id"],
    )
    with session_factory() as session:
        for table in ("llm_calls", "llm_cache", "cost_entries"):
            rows = session.execute(
                __import__("sqlalchemy").text(f"select * from {table}")
            ).fetchall()
            assert "UNIQUE-PROMPT-TEXT-XYZ" not in json.dumps(
                [tuple(map(str, r)) for r in rows]
            )


def test_pricing_table_and_unknown_model():
    book = PriceBook(usd_to_eur=1.0)
    cost = book.cost(
        "claude-haiku-4-5", tokens_in=1_000_000, tokens_out=100_000, web_searches=10
    )
    assert cost.usd == pytest.approx(1.0 + 0.5 + 0.1)
    with pytest.raises(PricingUnknownError):
        book.cost("claude-unknown-9")
    override = PriceBook(
        {"claude-unknown-9": {"input": 2.0, "output": 4.0}}, usd_to_eur=0.5
    )
    assert override.cost("claude-unknown-9", tokens_in=1_000_000).eur == pytest.approx(
        1.0
    )
    assert (
        book.estimate_max(
            "claude-opus-5", prompt_chars=3200, max_tokens=1000, web_searches=2
        ).web_searches
        == 2
    )


def test_extract_json_tolerates_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here you go: {"a": [1, 2]} thanks') == {"a": [1, 2]}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_backend_request_metadata_carries_task(settings, session_factory, project):
    seen = {}

    def scripter(request: BackendRequest):
        seen.update(request.metadata)
        return {"value": 1, "label": "a"}

    client, _ = _client(settings, session_factory, scripter)
    client.call(
        "storyboard",
        "p",
        project_id=project["project_id"],
        channel_id=project["channel_id"],
        metadata={"section_id": "s1"},
    )
    assert seen == {"task": "storyboard", "section_id": "s1"}
