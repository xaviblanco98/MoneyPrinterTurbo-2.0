"""LLM backends: the official Anthropic SDK and a scripted fake for tests.

A backend performs one model request and returns a normalized
``BackendResponse``. Everything else (model routing, caching, budget guard,
telemetry, structured-output validation) lives in ``mpt2.llm.client``.

The Anthropic backend follows the official SDK reference (anthropic 1.x):
``client.messages.create`` with ``output_config={"format": {"type":
"json_schema", ...}}`` for structured output, the ``web_search`` server tool
for research (results arrive as ``web_search_tool_result`` blocks, citations
on ``text`` blocks), ``pause_turn`` continuation, and the typed exception
classes for retry classification.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from pydantic import BaseModel, Field

from mpt2.errors import MPT2Error


class LLMBackendError(MPT2Error):
    code = "llm_backend_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool = False,
        status: int | None = None,
    ):
        super().__init__(message, code=code, module=__name__)
        self.retryable = retryable
        self.status = status


class WebSourceHit(BaseModel):
    url: str
    title: str = ""
    page_age: str | None = None
    cited_texts: list[str] = Field(default_factory=list)


@dataclass
class BackendRequest:
    model: str
    prompt: str
    system: str | None = None
    max_tokens: int = 4096
    temperature: float | None = None
    json_schema: dict[str, Any] | None = None
    web_search: dict[str, Any] | None = (
        None  # tool config (max_uses, allowed/blocked domains, ...)
    )
    effort: str | None = None
    metadata: dict[str, Any] = field(
        default_factory=dict
    )  # task name, context ids (never secrets)


@dataclass
class BackendResponse:
    text: str
    model: str
    provider: str
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    web_search_requests: int = 0
    sources: list[WebSourceHit] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    stop_reason: str | None = None
    request_id: str | None = None
    latency_ms: int = 0


class Backend(Protocol):
    name: str

    def complete(self, request: BackendRequest) -> BackendResponse: ...


# ----------------------------------------------------------------- Anthropic


def _model_supports_effort(model: str) -> bool:
    # Haiku 4.5 and older models reject output_config.effort.
    return not model.startswith("claude-haiku")


class AnthropicBackend:
    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 180.0,
        web_search_tool_version: str = "web_search_20250305",
        client: Any | None = None,
        max_pause_turn_continuations: int = 3,
    ):
        import anthropic  # official SDK; imported lazily so the fake backend needs no key

        self._anthropic = anthropic
        # Retries are handled by mpt2.llm.client with our own bounded backoff so
        # that every attempt is visible and budget-checked; disable SDK retries.
        self._client = client or anthropic.Anthropic(
            api_key=api_key, timeout=timeout_seconds, max_retries=0
        )
        self.web_search_tool_version = web_search_tool_version
        self.max_pause_turn_continuations = max_pause_turn_continuations

    # -- request building ---------------------------------------------------
    def _build_kwargs(
        self, request: BackendRequest, messages: list[dict[str, Any]]
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": messages,
        }
        if request.system:
            kwargs["system"] = request.system
        if request.temperature is not None and not request.model.startswith(
            ("claude-opus-5", "claude-sonnet-5", "claude-fable")
        ):
            # Sampling parameters are rejected on Opus 5 / Sonnet 5 / Fable.
            kwargs["temperature"] = request.temperature
        output_config: dict[str, Any] = {}
        if request.json_schema and not request.web_search:
            # Citations (always on for web search) are incompatible with output_config.format.
            output_config["format"] = {
                "type": "json_schema",
                "schema": request.json_schema,
            }
        if request.effort and _model_supports_effort(request.model):
            output_config["effort"] = request.effort
        if output_config:
            kwargs["output_config"] = output_config
        if request.web_search:
            tool: dict[str, Any] = {
                "type": self.web_search_tool_version,
                "name": "web_search",
            }
            for key in (
                "max_uses",
                "allowed_domains",
                "blocked_domains",
                "user_location",
            ):
                if request.web_search.get(key):
                    tool[key] = request.web_search[key]
            if self.web_search_tool_version != "web_search_20250305":
                # Newer variants default to dynamic filtering through code execution;
                # we want direct, predictable searches.
                tool["allowed_callers"] = ["direct"]
            kwargs["tools"] = [tool]
        return kwargs

    # -- error classification -----------------------------------------------
    def _wrap_error(self, exc: Exception) -> LLMBackendError:
        a = self._anthropic
        if isinstance(exc, a.RateLimitError):
            return LLMBackendError(
                "rate limited", code="llm_rate_limited", retryable=True, status=429
            )
        if isinstance(
            exc, (a.InternalServerError, a.OverloadedError, a.ServiceUnavailableError)
        ):
            return LLMBackendError(
                f"server error: {exc.__class__.__name__}",
                code="llm_server_error",
                retryable=True,
                status=getattr(exc, "status_code", 500),
            )
        if isinstance(exc, a.APITimeoutError):
            return LLMBackendError(
                "request timed out", code="llm_timeout", retryable=True
            )
        if isinstance(exc, a.APIConnectionError):
            return LLMBackendError(
                "connection error", code="llm_connection_error", retryable=True
            )
        if isinstance(exc, a.AuthenticationError):
            return LLMBackendError(
                "authentication failed (check ANTHROPIC_API_KEY)",
                code="llm_auth_error",
                status=401,
            )
        if isinstance(exc, a.PermissionDeniedError):
            return LLMBackendError(
                "permission denied", code="llm_permission_denied", status=403
            )
        if isinstance(exc, a.BadRequestError):
            return LLMBackendError(
                f"bad request: {getattr(exc, 'message', exc)}",
                code="llm_bad_request",
                status=400,
            )
        if isinstance(exc, a.APIStatusError):
            status = getattr(exc, "status_code", None)
            return LLMBackendError(
                f"api error {status}",
                code="llm_api_error",
                retryable=bool(status and status >= 500),
                status=status,
            )
        return LLMBackendError(
            f"{type(exc).__name__}: {exc}", code="llm_unexpected_error"
        )

    # -- response parsing ---------------------------------------------------
    @staticmethod
    def _collect(
        message: Any, response: BackendResponse, sources: dict[str, WebSourceHit]
    ) -> None:
        texts: list[str] = []
        for block in message.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                texts.append(block.text)
                for citation in getattr(block, "citations", None) or []:
                    if getattr(citation, "type", "") == "web_search_result_location":
                        hit = sources.setdefault(
                            citation.url,
                            WebSourceHit(url=citation.url, title=citation.title or ""),
                        )
                        if (
                            citation.cited_text
                            and citation.cited_text not in hit.cited_texts
                        ):
                            hit.cited_texts.append(citation.cited_text)
            elif btype == "server_tool_use":
                query = (
                    (getattr(block, "input", None) or {}).get("query")
                    if isinstance(getattr(block, "input", None), dict)
                    else None
                )
                if query:
                    response.search_queries.append(str(query))
            elif btype == "web_search_tool_result":
                content = block.content
                if isinstance(content, list):
                    for result in content:
                        if getattr(result, "type", "") == "web_search_result":
                            hit = sources.setdefault(
                                result.url,
                                WebSourceHit(url=result.url, title=result.title or ""),
                            )
                            if getattr(result, "page_age", None):
                                hit.page_age = result.page_age
                else:  # error object, not a list
                    code = getattr(content, "error_code", "unknown")
                    response.search_queries.append(f"<error:{code}>")
        response.text = (
            (response.text + "\n" + "\n".join(texts)).strip()
            if response.text
            else "\n".join(texts).strip()
        )
        usage = getattr(message, "usage", None)
        if usage is not None:
            response.tokens_in += int(getattr(usage, "input_tokens", 0) or 0)
            response.tokens_out += int(getattr(usage, "output_tokens", 0) or 0)
            response.cache_read_tokens += int(
                getattr(usage, "cache_read_input_tokens", 0) or 0
            )
            response.cache_write_tokens += int(
                getattr(usage, "cache_creation_input_tokens", 0) or 0
            )
            stu = getattr(usage, "server_tool_use", None)
            if stu is not None:
                response.web_search_requests += int(
                    getattr(stu, "web_search_requests", 0) or 0
                )

    def complete(self, request: BackendRequest) -> BackendResponse:
        messages: list[dict[str, Any]] = [{"role": "user", "content": request.prompt}]
        response = BackendResponse(text="", model=request.model, provider=self.name)
        sources: dict[str, WebSourceHit] = {}
        started = time.monotonic()
        continuations = 0
        while True:
            kwargs = self._build_kwargs(request, messages)
            try:
                message = self._client.messages.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 - classified below
                raise self._wrap_error(exc) from exc
            response.request_id = (
                getattr(message, "_request_id", None) or response.request_id
            )
            response.model = getattr(message, "model", request.model) or request.model
            self._collect(message, response, sources)
            response.stop_reason = getattr(message, "stop_reason", None)
            if response.stop_reason == "refusal":
                raise LLMBackendError("model refused the request", code="llm_refusal")
            if (
                response.stop_reason == "pause_turn"
                and continuations < self.max_pause_turn_continuations
            ):
                # Continue a long server-tool turn: send the paused assistant message back unchanged.
                continuations += 1
                messages.append({"role": "assistant", "content": message.content})
                continue
            break
        response.sources = list(sources.values())
        response.latency_ms = int((time.monotonic() - started) * 1000)
        return response


# ---------------------------------------------------------------------- Fake


Scripter = Callable[
    [BackendRequest], "dict[str, Any] | str | BackendResponse | Exception"
]


class FakeBackend:
    """Scripted backend for tests and offline runs.

    ``scripter(request)`` returns a dict (serialized as the JSON text), a plain
    string, a full ``BackendResponse`` or an exception to raise. Every request
    is recorded in ``calls``.
    """

    name = "fake"

    def __init__(
        self, scripter: Scripter, *, tokens_in: int = 500, tokens_out: int = 300
    ):
        self._scripter = scripter
        self.calls: list[BackendRequest] = []
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out

    def complete(self, request: BackendRequest) -> BackendResponse:
        import json

        self.calls.append(request)
        result = self._scripter(request)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, BackendResponse):
            return result
        text = json.dumps(result) if isinstance(result, dict) else str(result)
        return BackendResponse(
            text=text,
            model=request.model,
            provider=self.name,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            stop_reason="end_turn",
            latency_ms=1,
        )
