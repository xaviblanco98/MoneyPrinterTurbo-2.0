"""Unit tests of the Anthropic backend against fake SDK objects (no network)."""

from __future__ import annotations

from types import SimpleNamespace

import anthropic
import pytest

from mpt2.llm.backend import AnthropicBackend, BackendRequest, LLMBackendError


class _Messages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.kwargs = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(responses):
    return SimpleNamespace(messages=_Messages(responses))


def _text(text, citations=None):
    return SimpleNamespace(type="text", text=text, citations=citations or [])


def _citation(url, title, cited):
    return SimpleNamespace(
        type="web_search_result_location",
        url=url,
        title=title,
        cited_text=cited,
        encrypted_index="x",
    )


def _usage(i=10, o=5, searches=0, cr=0, cw=0):
    return SimpleNamespace(
        input_tokens=i,
        output_tokens=o,
        cache_read_input_tokens=cr,
        cache_creation_input_tokens=cw,
        server_tool_use=SimpleNamespace(web_search_requests=searches)
        if searches
        else None,
    )


def _message(content, stop="end_turn", usage=None, model="claude-haiku-4-5"):
    return SimpleNamespace(
        content=content,
        stop_reason=stop,
        usage=usage or _usage(),
        model=model,
        _request_id="req_1",
    )


def test_structured_request_uses_output_config_and_effort():
    client = _client([_message([_text('{"a": 1}')])])
    backend = AnthropicBackend("k", client=client)
    resp = backend.complete(
        BackendRequest(
            model="claude-opus-5",
            prompt="p",
            system="s",
            max_tokens=100,
            json_schema={"type": "object"},
            effort="high",
            temperature=0.2,
        )
    )
    kwargs = client.messages.kwargs[0]
    assert (
        kwargs["model"] == "claude-opus-5"
        and kwargs["system"] == "s"
        and kwargs["max_tokens"] == 100
    )
    assert kwargs["output_config"] == {
        "format": {"type": "json_schema", "schema": {"type": "object"}},
        "effort": "high",
    }
    assert "temperature" not in kwargs  # sampling params rejected on Opus 5
    assert (
        resp.text == '{"a": 1}' and resp.tokens_in == 10 and resp.request_id == "req_1"
    )


def test_haiku_gets_no_effort_but_temperature():
    client = _client([_message([_text("x")])])
    AnthropicBackend("k", client=client).complete(
        BackendRequest(
            model="claude-haiku-4-5", prompt="p", effort="high", temperature=0.3
        )
    )
    kwargs = client.messages.kwargs[0]
    assert "output_config" not in kwargs and kwargs["temperature"] == 0.3


def test_web_search_tool_definition_and_result_parsing():
    results = SimpleNamespace(
        type="web_search_tool_result",
        content=[
            SimpleNamespace(
                type="web_search_result",
                url="https://sec.gov/a",
                title="SEC filing",
                page_age="2025-03-01",
                encrypted_content="e",
            )
        ],
    )
    content = [
        _text("I'll search."),
        SimpleNamespace(
            type="server_tool_use",
            id="srv1",
            name="web_search",
            input={"query": "dealer margins"},
        ),
        results,
        _text(
            "Dealers make 12 percent.",
            citations=[
                _citation("https://sec.gov/a", "SEC filing", "make 12 percent on")
            ],
        ),
    ]
    client = _client([_message(content, usage=_usage(searches=1))])
    backend = AnthropicBackend(
        "k", client=client, web_search_tool_version="web_search_20250305"
    )
    resp = backend.complete(
        BackendRequest(
            model="claude-haiku-4-5",
            prompt="p",
            json_schema={"type": "object"},
            web_search={
                "max_uses": 3,
                "blocked_domains": ["bad.com"],
                "user_location": {"type": "approximate", "country": "US"},
            },
        )
    )
    kwargs = client.messages.kwargs[0]
    assert kwargs["tools"] == [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 3,
            "blocked_domains": ["bad.com"],
            "user_location": {"type": "approximate", "country": "US"},
        }
    ]
    assert (
        "output_config" not in kwargs
    )  # citations are incompatible with structured output
    assert resp.web_search_requests == 1 and resp.search_queries == ["dealer margins"]
    assert len(resp.sources) == 1
    assert (
        resp.sources[0].url == "https://sec.gov/a"
        and resp.sources[0].page_age == "2025-03-01"
    )
    assert resp.sources[0].cited_texts == ["make 12 percent on"]
    assert "Dealers make 12 percent." in resp.text


def test_newer_tool_version_forces_direct_calls():
    client = _client([_message([_text("x")])])
    AnthropicBackend(
        "k", client=client, web_search_tool_version="web_search_20260209"
    ).complete(
        BackendRequest(model="claude-opus-5", prompt="p", web_search={"max_uses": 1})
    )
    assert client.messages.kwargs[0]["tools"][0]["allowed_callers"] == ["direct"]


def test_web_search_error_object_is_not_a_crash():
    err = SimpleNamespace(
        type="web_search_tool_result",
        content=SimpleNamespace(
            type="web_search_tool_result_error", error_code="max_uses_exceeded"
        ),
    )
    client = _client([_message([err, _text("nothing found")])])
    resp = AnthropicBackend("k", client=client).complete(
        BackendRequest(model="m", prompt="p", web_search={"max_uses": 1})
    )
    assert resp.sources == [] and resp.search_queries == ["<error:max_uses_exceeded>"]


def test_pause_turn_is_continued_with_bounded_restarts():
    paused = _message([_text("part 1")], stop="pause_turn")
    final = _message([_text("part 2")], stop="end_turn")
    client = _client([paused, final])
    resp = AnthropicBackend(
        "k", client=client, max_pause_turn_continuations=3
    ).complete(BackendRequest(model="m", prompt="p", web_search={"max_uses": 2}))
    assert len(client.messages.kwargs) == 2
    assert (
        client.messages.kwargs[1]["messages"][1]["role"] == "assistant"
    )  # paused turn sent back unchanged
    assert resp.text == "part 1\npart 2" and resp.tokens_in == 20


def test_refusal_raises():
    client = _client([_message([_text("")], stop="refusal")])
    with pytest.raises(LLMBackendError) as exc:
        AnthropicBackend("k", client=client).complete(
            BackendRequest(model="m", prompt="p")
        )
    assert exc.value.code == "llm_refusal" and exc.value.retryable is False


def _status_error(cls, status):
    request = SimpleNamespace(method="POST", url="u")
    response = SimpleNamespace(status_code=status, headers={}, request=request)
    try:
        return cls(message="m", response=response, body=None)
    except TypeError:  # pragma: no cover - SDK signature drift
        return cls("m")


@pytest.mark.parametrize(
    "exc,code,retryable",
    [
        (_status_error(anthropic.RateLimitError, 429), "llm_rate_limited", True),
        (_status_error(anthropic.InternalServerError, 500), "llm_server_error", True),
        (_status_error(anthropic.AuthenticationError, 401), "llm_auth_error", False),
        (_status_error(anthropic.BadRequestError, 400), "llm_bad_request", False),
        (
            anthropic.APIConnectionError(
                request=SimpleNamespace(method="POST", url="u")
            ),
            "llm_connection_error",
            True,
        ),
        (RuntimeError("boom"), "llm_unexpected_error", False),
    ],
)
def test_error_classification(exc, code, retryable):
    client = _client([exc])
    with pytest.raises(LLMBackendError) as raised:
        AnthropicBackend("k", client=client).complete(
            BackendRequest(model="m", prompt="p")
        )
    assert raised.value.code == code and raised.value.retryable is retryable


def test_sdk_client_is_built_with_timeout_and_no_sdk_retries(monkeypatch):
    captured = {}

    class Dummy:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.messages = None

    monkeypatch.setattr(anthropic, "Anthropic", Dummy)
    AnthropicBackend("secret", timeout_seconds=42.0)
    assert captured == {"api_key": "secret", "timeout": 42.0, "max_retries": 0}
