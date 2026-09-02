"""LLM providers for mpt2.

``UpstreamLLMProvider`` reuses the upstream MoneyPrinterTurbo provider
registry (``app/services/llm.py``), which already knows how to talk to
Ollama and twenty other OpenAI-compatible backends. Configuration comes from
mpt2 settings (environment), not from ``config.toml``. Upstream returns
errors as strings starting with ``"Error: "``; the adapter turns those into
exceptions so callers never mistake an error for content.
"""

from __future__ import annotations

from typing import Any, Callable

from mpt2.contracts import LLMRequest, LLMResponse
from mpt2.errors import StageError
from mpt2.settings import Settings

Backend = Callable[[str, dict[str, Any]], str]


def _upstream_backend(prompt: str, app_config: dict[str, Any]) -> str:
    from app.services import (
        llm as upstream_llm,
    )  # lazy: importing upstream loads config.toml

    return upstream_llm._generate_response(prompt, app_config=app_config)


class UpstreamLLMProvider:
    """``LLMProvider`` adapter over the upstream provider registry."""

    def __init__(self, settings: Settings, backend: Backend | None = None):
        self.settings = settings
        self.name = settings.llm_provider
        self._backend = backend or _upstream_backend

    def app_config(self) -> dict[str, Any]:
        """Build the config snapshot upstream expects, from environment settings."""
        provider = self.settings.llm_provider
        cfg: dict[str, Any] = {"llm_provider": provider}
        if provider == "ollama":
            cfg["ollama_base_url"] = self.settings.ollama_base_url
            cfg["ollama_model_name"] = self.settings.ollama_model
        else:
            cfg[f"{provider}_api_key"] = self.settings.llm_api_key or ""
            cfg[f"{provider}_base_url"] = self.settings.llm_base_url
            cfg[f"{provider}_model_name"] = self.settings.llm_model
        return cfg

    def generate(self, request: LLMRequest) -> LLMResponse:
        prompt = (
            request.prompt
            if not request.system
            else f"{request.system}\n\n{request.prompt}"
        )
        text = self._backend(prompt, self.app_config())
        if not isinstance(text, str) or text.startswith("Error: "):
            message = (
                text.removeprefix("Error: ")
                if isinstance(text, str)
                else "empty response"
            )
            raise StageError(
                message or "LLM returned an error", code="llm_error", module=__name__
            )
        model = (
            self.settings.ollama_model
            if self.name == "ollama"
            else self.settings.llm_model
        )
        return LLMResponse(text=text, provider=self.name, model=model or "default")
