"""Price table and cost estimation.

Prices are USD per million tokens (Anthropic first-party API, cached in the
claude-api reference on 2026-06-24). They can be extended or overridden at
runtime with ``MPT2_LLM_PRICING_JSON`` so a new model never runs unpriced:
an unknown model raises ``PricingUnknownError`` and the call is blocked,
because unregistered spend is not allowed.
"""

from __future__ import annotations

from dataclasses import dataclass

from mpt2.errors import MPT2Error

WEB_SEARCH_USD_PER_REQUEST = 10.0 / 1000.0  # $10 per 1,000 searches

# input, output, cache_write (1.25x input), cache_read (0.1x input)
PRICES_USD_PER_M: dict[str, dict[str, float]] = {
    "claude-opus-5": {
        "input": 5.0,
        "output": 25.0,
        "cache_write": 6.25,
        "cache_read": 0.5,
    },
    "claude-opus-4-8": {
        "input": 5.0,
        "output": 25.0,
        "cache_write": 6.25,
        "cache_read": 0.5,
    },
    "claude-opus-4-7": {
        "input": 5.0,
        "output": 25.0,
        "cache_write": 6.25,
        "cache_read": 0.5,
    },
    "claude-opus-4-6": {
        "input": 5.0,
        "output": 25.0,
        "cache_write": 6.25,
        "cache_read": 0.5,
    },
    "claude-sonnet-5": {
        "input": 2.0,
        "output": 10.0,
        "cache_write": 2.5,
        "cache_read": 0.2,
    },
    "claude-sonnet-4-6": {
        "input": 3.0,
        "output": 15.0,
        "cache_write": 3.75,
        "cache_read": 0.3,
    },
    "claude-haiku-4-5": {
        "input": 1.0,
        "output": 5.0,
        "cache_write": 1.25,
        "cache_read": 0.1,
    },
    "claude-fable-5-1": {
        "input": 10.0,
        "output": 50.0,
        "cache_write": 12.5,
        "cache_read": 0.25,
    },
    # Test/offline backends cost nothing but are still recorded.
    "fake": {"input": 0.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0},
}


class PricingUnknownError(MPT2Error):
    code = "pricing_unknown"


@dataclass(frozen=True)
class CostBreakdown:
    usd: float
    eur: float
    tokens_in: int
    tokens_out: int
    cache_read: int
    cache_write: int
    web_searches: int


class PriceBook:
    def __init__(
        self,
        overrides: dict[str, dict[str, float]] | None = None,
        usd_to_eur: float = 0.92,
    ):
        self._prices = {k: dict(v) for k, v in PRICES_USD_PER_M.items()}
        for model, values in (overrides or {}).items():
            merged = dict(self._prices.get(model, {}))
            merged.update({k: float(v) for k, v in values.items()})
            self._prices[model] = merged
        self.usd_to_eur = usd_to_eur

    def has(self, model: str) -> bool:
        return model in self._prices

    def prices(self, model: str) -> dict[str, float]:
        try:
            return self._prices[model]
        except KeyError as exc:
            raise PricingUnknownError(
                f"no price registered for model {model!r}; add it with MPT2_LLM_PRICING_JSON",
                module=__name__,
            ) from exc

    def cost(
        self,
        model: str,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cache_read: int = 0,
        cache_write: int = 0,
        web_searches: int = 0,
    ) -> CostBreakdown:
        p = self.prices(model)
        usd = (
            tokens_in * p.get("input", 0.0)
            + tokens_out * p.get("output", 0.0)
            + cache_read * p.get("cache_read", 0.0)
            + cache_write * p.get("cache_write", 0.0)
        ) / 1_000_000.0
        usd += web_searches * WEB_SEARCH_USD_PER_REQUEST
        return CostBreakdown(
            usd=round(usd, 6),
            eur=round(usd * self.usd_to_eur, 6),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cache_read=cache_read,
            cache_write=cache_write,
            web_searches=web_searches,
        )

    def estimate_max(
        self, model: str, *, prompt_chars: int, max_tokens: int, web_searches: int = 0
    ) -> CostBreakdown:
        """Worst-case cost before a call: rough input tokens + full max_tokens output."""
        tokens_in = int(prompt_chars / 3.2) + 200  # conservative for English + JSON
        # each web search injects roughly 2-4k tokens of results
        tokens_in += web_searches * 4000
        return self.cost(
            model, tokens_in=tokens_in, tokens_out=max_tokens, web_searches=web_searches
        )
