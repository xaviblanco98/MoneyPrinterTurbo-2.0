"""LLMClient: model routing per task, structured outputs, cache, budget guard,
bounded retries and full telemetry. This is the only path to the model.

Rules enforced here:
- The model is chosen from settings (task -> tier -> model); never hard-coded.
- Every call is budget-checked with a worst-case estimate before it runs.
- Every completed call records tokens, model, latency and cost (``llm_calls``
  and ``cost_entries``). Cache hits are recorded too, at zero cost.
- Prompts are never persisted; only a SHA-256 and the length. A prompt that
  contains the API key is refused.
- Structured outputs are validated with Pydantic. One repair attempt is made
  with the validation error; then the call fails.
- Retries are bounded (``llm_max_retries``) with exponential backoff and only
  for retryable backend errors.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from loguru import logger
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from mpt2.costs.guard import BudgetExceededError, BudgetGuard
from mpt2.errors import StageError
from mpt2.llm.backend import (
    Backend,
    BackendRequest,
    BackendResponse,
    LLMBackendError,
    WebSourceHit,
)
from mpt2.llm.pricing import PriceBook
from mpt2.models import CostEntry, LLMCache, LLMCall, utcnow
from mpt2.models.enums import CostUnit, LLMCallStatus
from mpt2.settings import Settings

T = TypeVar("T", bound=BaseModel)

# Task -> tier. "fast" for classification/extraction/simple tasks, "smart" for
# research, reasoning and the final script. Overridable with MPT2_LLM_TASK_TIERS.
DEFAULT_TASK_TIERS: dict[str, str] = {
    "research_plan": "smart",
    "search_queries": "fast",
    "web_search": "fast",
    "source_screening": "fast",
    "dossier": "smart",
    "claim_extraction": "fast",
    "fact_check": "smart",
    "angles_hooks": "smart",
    "script_write": "smart",
    "script_repair": "smart",
    "storyboard": "fast",
    "editorial_qc": "smart",
}


@dataclass
class LLMResult:
    text: str
    parsed: Any
    model: str
    provider: str
    cache_hit: bool
    cost_eur: float
    tokens_in: int
    tokens_out: int
    web_search_requests: int
    sources: list[WebSourceHit]
    search_queries: list[str]
    latency_ms: int
    call_id: str | None


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


def extract_json(text: str) -> Any:
    """Parse JSON from a model response, tolerating code fences and prose around it."""
    candidate = _strip_code_fence(text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    start = min(
        [i for i in (candidate.find("{"), candidate.find("[")) if i >= 0], default=-1
    )
    if start < 0:
        raise ValueError("no JSON object found in response")
    end = max(candidate.rfind("}"), candidate.rfind("]"))
    return json.loads(candidate[start : end + 1])


class LLMClient:
    def __init__(
        self,
        settings: Settings,
        backend: Backend,
        session_factory: Callable[[], Session],
        budget: BudgetGuard | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.settings = settings
        self.backend = backend
        self._session_factory = session_factory
        self.budget = budget or BudgetGuard(settings)
        self.prices = PriceBook(
            settings.pricing_overrides(), usd_to_eur=settings.usd_to_eur
        )
        self._sleep = sleep
        self._tiers = {**DEFAULT_TASK_TIERS, **settings.task_tiers()}
        self._orphans: list[dict[str, Any]] = []
        self._task_models = settings.task_models()
        self._task_effort = settings.task_effort()

    # ------------------------------------------------------------ routing
    def model_for(self, task: str) -> str:
        if task in self._task_models:
            return self._task_models[task]
        tier = self._tiers.get(task, "smart")
        if tier not in {"fast", "smart"}:
            raise StageError(
                f"unknown tier {tier!r} for task {task!r}",
                code="llm_config",
                module=__name__,
                retryable=False,
            )
        model = (
            self.settings.model_fast if tier == "fast" else self.settings.model_smart
        )
        if self.backend.name == "fake":
            return "fake"
        return model

    def effort_for(self, task: str) -> str | None:
        return self._task_effort.get(task)

    # -------------------------------------------------------------- cache
    @staticmethod
    def cache_key(request: BackendRequest) -> str:
        payload = {
            "model": request.model,
            "system": request.system,
            "prompt": request.prompt,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "schema": request.json_schema,
            "web_search": request.web_search,
            "effort": request.effort,
        }
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # --------------------------------------------------------------- call
    def call(
        self,
        task: str,
        prompt: str,
        *,
        schema: type[T] | None = None,
        system: str | None = None,
        project_id: str | None = None,
        channel_id: str | None = None,
        stage: str | None = None,
        max_tokens: int = 4096,
        temperature: float | None = None,
        web_search: dict[str, Any] | None = None,
        use_cache: bool | None = None,
        metadata: dict[str, Any] | None = None,
        session: Session | None = None,
    ) -> LLMResult:
        """Run one model call. When ``session`` is given (stage handlers), telemetry,
        cache and budget checks use that transaction so SQLite never sees two writers."""
        self._refuse_secrets(prompt, system)
        model = self.model_for(task)
        request = BackendRequest(
            model=model,
            prompt=prompt,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            json_schema=schema.model_json_schema() if schema else None,
            web_search=web_search,
            effort=self.effort_for(task),
            metadata={"task": task, **(metadata or {})},
        )
        key = self.cache_key(request)
        idempotency_key = f"{project_id or 'global'}:{task}:{key[:16]}"
        cache_on = self.settings.llm_cache_enabled if use_cache is None else use_cache

        if cache_on:
            cached = self._cache_get(key, session=session)
            if cached is not None:
                parsed = self._parse(schema, cached["text"]) if schema else None
                self._record_call(
                    task=task,
                    stage=stage,
                    project_id=project_id,
                    channel_id=channel_id,
                    model=model,
                    request=request,
                    response=None,
                    cache_key=key,
                    idempotency_key=idempotency_key,
                    cache_hit=True,
                    cost_eur=0.0,
                    cost_usd=0.0,
                    status=LLMCallStatus.ok,
                    session=session,
                )
                return LLMResult(
                    text=cached["text"],
                    parsed=parsed,
                    model=model,
                    provider=self.backend.name,
                    cache_hit=True,
                    cost_eur=0.0,
                    tokens_in=0,
                    tokens_out=0,
                    web_search_requests=0,
                    sources=[WebSourceHit(**s) for s in cached.get("sources", [])],
                    search_queries=list(cached.get("search_queries", [])),
                    latency_ms=0,
                    call_id=None,
                )

        response = self._complete_with_budget(
            request,
            task=task,
            stage=stage,
            project_id=project_id,
            channel_id=channel_id,
            key=key,
            idempotency_key=idempotency_key,
            session=session,
        )
        parsed = None
        if schema:
            try:
                parsed = self._parse(schema, response.text)
            except (ValueError, ValidationError) as exc:
                logger.warning(
                    f"llm task {task}: invalid structured output, attempting one repair: {str(exc)[:200]}"
                )
                repair = BackendRequest(
                    model=model,
                    prompt=self._repair_prompt(prompt, response.text, str(exc)),
                    system=system,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    json_schema=request.json_schema,
                    web_search=None,
                    effort=request.effort,
                    metadata={**request.metadata, "repair": True},
                )
                response = self._complete_with_budget(
                    repair,
                    task=task,
                    stage=stage,
                    project_id=project_id,
                    channel_id=channel_id,
                    key=self.cache_key(repair),
                    idempotency_key=idempotency_key + ":repair",
                    session=session,
                )
                try:
                    parsed = self._parse(schema, response.text)
                except (ValueError, ValidationError) as exc2:
                    raise StageError(
                        f"llm task {task}: output does not match schema after repair: {str(exc2)[:300]}",
                        code="llm_invalid_output",
                        module=__name__,
                    ) from exc2
        if cache_on:
            self._cache_put(key, model, task, response, session=session)
        cost = self.prices.cost(
            model,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            cache_read=response.cache_read_tokens,
            cache_write=response.cache_write_tokens,
            web_searches=response.web_search_requests,
        )
        return LLMResult(
            text=response.text,
            parsed=parsed,
            model=response.model or model,
            provider=response.provider,
            cache_hit=False,
            cost_eur=cost.eur,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            web_search_requests=response.web_search_requests,
            sources=response.sources,
            search_queries=response.search_queries,
            latency_ms=response.latency_ms,
            call_id=getattr(response, "call_id", None),
        )

    # ------------------------------------------------------------ helpers
    def _refuse_secrets(self, prompt: str, system: str | None) -> None:
        secrets = [
            s
            for s in (
                self.settings.anthropic_api_key,
                self.settings.api_key,
                self.settings.llm_api_key,
                self.settings.pexels_api_key,
                self.settings.pixabay_api_key,
            )
            if s
        ]
        blob = (system or "") + "\n" + prompt
        for secret in secrets:
            if secret and secret in blob:
                raise StageError(
                    "prompt contains a configured secret; refusing to send it",
                    code="prompt_contains_secret",
                    module=__name__,
                    retryable=False,
                )

    @staticmethod
    def _parse(schema: type[T], text: str) -> T:
        data = extract_json(text)
        return schema.model_validate(data)

    @staticmethod
    def _repair_prompt(original: str, bad_output: str, error: str) -> str:
        return (
            f"{original}\n\n---\nYour previous answer was not valid for the required JSON schema.\n"
            f"Validation error: {error[:1500]}\n"
            f"Previous answer (truncated): {bad_output[:3000]}\n"
            "Return ONLY a corrected JSON object that satisfies the schema exactly."
        )

    def _complete_with_budget(
        self,
        request: BackendRequest,
        *,
        task: str,
        stage: str | None,
        project_id: str | None,
        channel_id: str | None,
        key: str,
        idempotency_key: str,
        session: Session | None = None,
    ) -> BackendResponse:
        searches = int((request.web_search or {}).get("max_uses", 0) or 0)
        estimate = self.prices.estimate_max(
            request.model,
            prompt_chars=len(request.prompt) + len(request.system or ""),
            max_tokens=request.max_tokens,
            web_searches=searches,
        )

        def _check(sess: Session) -> None:
            try:
                self.budget.check(
                    sess,
                    estimated_eur=estimate.eur,
                    project_id=project_id,
                    what=f"llm:{task}",
                )
            except BudgetExceededError as exc:
                self._record_call(
                    task=task,
                    stage=stage,
                    project_id=project_id,
                    channel_id=channel_id,
                    model=request.model,
                    request=request,
                    response=None,
                    cache_key=key,
                    idempotency_key=idempotency_key,
                    cache_hit=False,
                    cost_eur=0.0,
                    cost_usd=0.0,
                    status=LLMCallStatus.blocked,
                    error=exc,
                    session=sess,
                )
                if sess is not session:
                    sess.commit()
                raise StageError(
                    exc.message, code=exc.code, module=__name__, retryable=False
                ) from exc

        if session is not None:
            _check(session)
        else:
            with self._session_factory() as sess:
                _check(sess)

        attempts = 0
        while True:
            attempts += 1
            try:
                response = self.backend.complete(request)
                break
            except LLMBackendError as exc:
                self._record_call(
                    task=task,
                    stage=stage,
                    project_id=project_id,
                    channel_id=channel_id,
                    model=request.model,
                    request=request,
                    response=None,
                    cache_key=key,
                    idempotency_key=idempotency_key,
                    cache_hit=False,
                    cost_eur=0.0,
                    cost_usd=0.0,
                    status=LLMCallStatus.error,
                    error=exc,
                    session=session,
                )
                if exc.retryable and attempts <= self.settings.llm_max_retries:
                    delay = self.settings.llm_retry_base_seconds * (2 ** (attempts - 1))
                    logger.warning(
                        f"llm task {task}: {exc.code}, retry {attempts}/{self.settings.llm_max_retries} in {delay:.1f}s"
                    )
                    self._sleep(delay)
                    continue
                raise StageError(
                    exc.message, code=exc.code, module=__name__, retryable=False
                ) from exc

        cost = self.prices.cost(
            request.model,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            cache_read=response.cache_read_tokens,
            cache_write=response.cache_write_tokens,
            web_searches=response.web_search_requests,
        )
        call_id = self._record_call(
            task=task,
            stage=stage,
            project_id=project_id,
            channel_id=channel_id,
            model=response.model or request.model,
            request=request,
            response=response,
            cache_key=key,
            idempotency_key=idempotency_key,
            cache_hit=False,
            cost_eur=cost.eur,
            cost_usd=cost.usd,
            status=LLMCallStatus.ok,
            session=session,
        )
        response.call_id = call_id  # type: ignore[attr-defined]
        return response

    def _record_call(
        self,
        *,
        task: str,
        stage: str | None,
        project_id: str | None,
        channel_id: str | None,
        model: str,
        request: BackendRequest,
        response: BackendResponse | None,
        cache_key: str,
        idempotency_key: str,
        cache_hit: bool,
        cost_eur: float,
        cost_usd: float,
        status: LLMCallStatus,
        error: Exception | None = None,
        session: Session | None = None,
    ) -> str:
        prompt_blob = (request.system or "") + "\n" + request.prompt
        call = LLMCall(
            project_id=project_id,
            channel_id=channel_id,
            task=task,
            stage=stage,
            model=model,
            provider=self.backend.name,
            idempotency_key=idempotency_key,
            cache_key=cache_key,
            cache_hit=cache_hit,
            prompt_sha256=hashlib.sha256(prompt_blob.encode("utf-8")).hexdigest(),
            prompt_chars=len(prompt_blob),
            tokens_in=response.tokens_in if response else 0,
            tokens_out=response.tokens_out if response else 0,
            cache_read_tokens=response.cache_read_tokens if response else 0,
            cache_write_tokens=response.cache_write_tokens if response else 0,
            web_search_requests=response.web_search_requests if response else 0,
            latency_ms=response.latency_ms if response else 0,
            cost_usd=cost_usd,
            cost_eur=cost_eur,
            status=status,
            error_code=getattr(error, "code", None) if error else None,
            error_message=str(getattr(error, "message", error))[:2000]
            if error
            else None,
            request_id=response.request_id if response else None,
            stop_reason=response.stop_reason if response else None,
        )

        def _write(sess: Session) -> str:
            sess.add(call)
            if status == LLMCallStatus.ok and not cache_hit and channel_id:
                sess.add(
                    CostEntry(
                        project_id=project_id,
                        channel_id=channel_id,
                        provider=f"{self.backend.name}:{model}",
                        stage=stage or task,
                        units=float(
                            (response.tokens_in if response else 0)
                            + (response.tokens_out if response else 0)
                        ),
                        unit_type=CostUnit.tokens,
                        est_cost_eur=cost_eur,
                        note=json.dumps(
                            {
                                "task": task,
                                "web_search_requests": response.web_search_requests
                                if response
                                else 0,
                                "usd": cost_usd,
                            }
                        ),
                    )
                )
            sess.flush()
            return call.id

        if session is not None:
            if status != LLMCallStatus.ok:
                # The caller's transaction may be rolled back after this error; keep a
                # copy so the pipeline can re-record it (see ``replay_orphans``).
                self._orphans.append(self._clone_call(call))
            return _write(session)
        with self._session_factory() as sess:
            call_id = _write(sess)
            sess.commit()
            return call_id

    @staticmethod
    def _clone_call(call: LLMCall) -> dict[str, Any]:
        return {
            c.name: getattr(call, c.name)
            for c in LLMCall.__table__.columns
            if c.name != "id"
        }

    def replay_orphans(self, session: Session) -> int:
        """Re-insert error/blocked telemetry lost to a rollback. Idempotent per idempotency key + created_at."""
        count = 0
        for data in self._orphans:
            exists = session.scalar(
                select(LLMCall).where(
                    LLMCall.idempotency_key == data["idempotency_key"],
                    LLMCall.created_at == data["created_at"],
                )
            )
            if exists is None:
                session.add(LLMCall(**data))
                count += 1
        self._orphans.clear()
        session.flush()
        return count

    def _cache_get(
        self, key: str, *, session: Session | None = None
    ) -> dict[str, Any] | None:
        def _get(sess: Session) -> dict[str, Any] | None:
            row = sess.get(LLMCache, key)
            if row is None:
                return None
            row.hits = (row.hits or 0) + 1
            row.last_hit_at = utcnow()
            return dict(row.response)

        if session is not None:
            return _get(session)
        with self._session_factory() as sess:
            data = _get(sess)
            sess.commit()
            return data

    def _cache_put(
        self,
        key: str,
        model: str,
        task: str,
        response: BackendResponse,
        *,
        session: Session | None = None,
    ) -> None:
        if session is not None:
            if session.get(LLMCache, key) is None:
                session.add(self._cache_row(key, model, task, response))
                session.flush()
            return
        with self._session_factory() as sess:
            if sess.get(LLMCache, key) is not None:
                return
            sess.add(self._cache_row(key, model, task, response))
            sess.commit()

    @staticmethod
    def _cache_row(
        key: str, model: str, task: str, response: BackendResponse
    ) -> LLMCache:
        return LLMCache(
            cache_key=key,
            model=model,
            task=task,
            response={
                "text": response.text,
                "sources": [s.model_dump() for s in response.sources],
                "search_queries": list(response.search_queries),
            },
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
        )
