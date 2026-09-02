from __future__ import annotations

from mpt2.contracts import SearchContext, SearchResult, SourceCandidate
from mpt2.errors import StageError
from mpt2.llm.backend import WebSourceHit
from mpt2.llm.client import LLMResult
from mpt2.research.providers import AnthropicWebSearchProvider, FakeResearchProvider
from mpt2.research.urls import (
    canonical_url,
    domain_of,
    is_banned,
    looks_like_domain,
    looks_primary,
)


def test_canonical_url_and_domain():
    a = canonical_url("https://WWW.Example.com/path/?utm_source=x&b=2&a=1#frag")
    b = canonical_url("https://example.com/path?a=1&b=2")
    assert a == b == "https://example.com/path?a=1&b=2"
    assert domain_of("https://www.sec.gov/x") == "sec.gov"
    assert (
        looks_like_domain("sec.gov")
        and looks_like_domain("example.com/blog")
        and not looks_like_domain("annual reports")
    )
    assert is_banned(
        "news.tabloid.example.test", ["tabloid.example.test"]
    ) and not is_banned("tabloid.example.test.evil.com", ["tabloid.example.test"])
    assert looks_primary(
        "https://www.sec.gov/Archives/edgar/data/x", "sec.gov"
    ) and not looks_primary("https://blog.example.com/a", "blog.example.com")


class _LLMStub:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def call(self, task, prompt, **kwargs):
        self.calls.append((task, kwargs))
        if self.error:
            raise self.error
        return self.result


def _result(sources, queries=("q1",), text="summary"):
    return LLMResult(
        text=text,
        parsed=None,
        model="m",
        provider="anthropic",
        cache_hit=False,
        cost_eur=0.02,
        tokens_in=1,
        tokens_out=1,
        web_search_requests=len(queries),
        sources=sources,
        search_queries=list(queries),
        latency_ms=1,
        call_id=None,
    )


def test_anthropic_provider_dedups_filters_and_records_cost():
    hits = [
        WebSourceHit(
            url="https://www.sec.gov/doc?utm_source=a",
            title="SEC",
            cited_texts=["Dealers earned 12 percent on F&I products in 2024."],
            page_age="2025-01-01",
        ),
        WebSourceHit(
            url="https://sec.gov/doc", title="SEC dup", cited_texts=["another fragment"]
        ),
        WebSourceHit(
            url="https://tabloid.example.test/x",
            title="Tabloid",
            cited_texts=["sensational"],
        ),
        WebSourceHit(url="https://news.example.test/y", title="News", cited_texts=[]),
    ]
    llm = _LLMStub(
        result=_result(
            hits, queries=["dealer margins 2024", "<error:max_uses_exceeded>"]
        )
    )
    provider = AnthropicWebSearchProvider(llm, max_uses_per_call=2, country="US")
    ctx = SearchContext(
        project_id="p",
        channel_id="c",
        stage="research_search",
        blocked_domains=["tabloid.example.test"],
        country=None,
    )
    result = provider.search("dealer margins", language="en-US", context=ctx)
    assert isinstance(result, SearchResult) and result.error is None
    urls = [c.url for c in result.candidates]
    assert urls == [
        "https://www.sec.gov/doc?utm_source=a",
        "https://news.example.test/y",
    ]  # dedup by canonical url, tabloid banned
    assert result.candidates[0].snippets == [
        "Dealers earned 12 percent on F&I products in 2024."
    ]
    assert result.executed_queries == ["dealer margins 2024"]
    assert result.cost_eur == 0.02
    task, kwargs = llm.calls[0]
    assert task == "web_search"
    assert kwargs["web_search"] == {
        "max_uses": 2,
        "blocked_domains": ["tabloid.example.test"],
        "user_location": {"type": "approximate", "country": "US"},
    }


def test_anthropic_provider_uses_allowed_domains_when_restricting():
    llm = _LLMStub(result=_result([]))
    provider = AnthropicWebSearchProvider(llm)
    ctx = SearchContext(
        allowed_domains=["sec.gov", "not a domain"], blocked_domains=["x.com"]
    )
    result = provider.search("q", language="en", context=ctx)
    assert llm.calls[0][1]["web_search"]["allowed_domains"] == ["sec.gov"]
    assert "blocked_domains" not in llm.calls[0][1]["web_search"]  # never both
    assert result.candidates == [] and result.error == "no sources returned"


def test_anthropic_provider_never_fabricates_on_failure():
    llm = _LLMStub(error=StageError("budget", code="budget_exceeded"))
    provider = AnthropicWebSearchProvider(llm)
    result = provider.search("q", language="en", context=SearchContext())
    assert result.candidates == [] and result.error.startswith("budget_exceeded")


def test_fake_provider_marks_synthetic_sources_and_respects_bans():
    provider = FakeResearchProvider(fail_queries={"broken"})
    ctx = SearchContext(blocked_domains=["tabloid.example.test"])
    result = provider.search("dealer margins", language="en", context=ctx)
    assert all("[FAKE" in c.title for c in result.candidates)
    assert all(c.domain != "tabloid.example.test" for c in result.candidates)
    assert (
        provider.search("broken", language="en", context=ctx).error
        == "simulated search failure"
    )
    assert isinstance(result.candidates[0], SourceCandidate)
