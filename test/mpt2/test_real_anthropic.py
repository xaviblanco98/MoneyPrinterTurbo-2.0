"""Optional live test against the Anthropic API.

Runs only when MPT2_RUN_REAL_LLM_TESTS=1 and ANTHROPIC_API_KEY are set. It
makes one small structured call and one web search, costing well under 0.05 EUR.
"""

from __future__ import annotations

import os

import pytest
from pydantic import BaseModel

from mpt2.costs.guard import BudgetGuard
from mpt2.llm.backend import AnthropicBackend
from mpt2.llm.client import LLMClient
from test.mpt2.conftest import make_settings

pytestmark = pytest.mark.skipif(
    os.environ.get("MPT2_RUN_REAL_LLM_TESTS") != "1"
    or not os.environ.get("ANTHROPIC_API_KEY"),
    reason="set MPT2_RUN_REAL_LLM_TESTS=1 and ANTHROPIC_API_KEY to run the live test",
)


class Capital(BaseModel):
    country: str
    capital: str


def test_live_structured_and_web_search(tmp_path, session_factory):
    settings = make_settings(
        tmp_path,
        MPT2_LLM_BACKEND="anthropic",
        ANTHROPIC_API_KEY=os.environ["ANTHROPIC_API_KEY"],
    )
    backend = AnthropicBackend(
        settings.anthropic_api_key,
        timeout_seconds=settings.llm_timeout_seconds,
        web_search_tool_version=settings.web_search_tool_version,
    )
    client = LLMClient(settings, backend, session_factory, BudgetGuard(settings))
    result = client.call(
        "claim_extraction",
        "Return the capital of France.",
        schema=Capital,
        max_tokens=200,
    )
    assert result.parsed.capital.lower().startswith("paris")
    assert result.tokens_in > 0 and result.cost_eur > 0
    search = client.call(
        "web_search",
        "Search the web for the current US federal funds rate and cite the source.",
        web_search={"max_uses": 1},
        max_tokens=800,
    )
    assert search.web_search_requests >= 1 and search.sources
