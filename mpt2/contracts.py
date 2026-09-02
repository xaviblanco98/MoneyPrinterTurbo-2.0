"""Contracts (interfaces) for the future editorial modules.

These are deliberately small and runtime-checkable ``Protocol`` classes with
typed request/response models. Implementations arrive in later milestones;
H1 only guarantees that every module will be interchangeable behind a stable,
testable boundary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------- payloads


class SourceCandidate(BaseModel):
    url: str
    title: str
    publisher: str | None = None
    author: str | None = None
    published_at: str | None = None
    snippet: str | None = None
    snippets: list[str] = Field(default_factory=list)
    reliability: int = Field(default=3, ge=1, le=5)
    domain: str | None = None
    page_age: str | None = None


class SearchContext(BaseModel):
    """Per-project context a research provider needs (cost attribution, policy)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    session: Any = Field(
        default=None, exclude=True, description="DB session of the running job"
    )
    project_id: str | None = None
    channel_id: str | None = None
    stage: str | None = None
    blocked_domains: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    country: str | None = None


class SearchResult(BaseModel):
    query: str
    provider: str
    candidates: list[SourceCandidate] = Field(default_factory=list)
    executed_queries: list[str] = Field(default_factory=list)
    summary: str = ""
    cost_eur: float = 0.0
    error: str | None = None


class LLMRequest(BaseModel):
    prompt: str = Field(min_length=1)
    system: str | None = None
    json_schema: dict[str, Any] | None = None
    temperature: float = Field(default=0.4, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1)


class LLMResponse(BaseModel):
    text: str
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0


class ClaimInput(BaseModel):
    claim_id: str
    text: str
    source_urls: list[str] = Field(default_factory=list)


class FactCheckFinding(BaseModel):
    claim_id: str
    status: str = Field(pattern=r"^(pass|warn|block)$")
    reason: str


class FactCheckResult(BaseModel):
    findings: list[FactCheckFinding] = Field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(f.status == "block" for f in self.findings)


class SceneSpec(BaseModel):
    position: int = Field(ge=0)
    narration: str
    est_duration_s: float = Field(ge=0.0)
    visual_description: str = ""
    asset_type: str = "stock_footage"
    search_terms: list[str] = Field(default_factory=list)
    on_screen_text: str | None = None
    transition: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    fallback_visual: str | None = None


class StoryboardSpec(BaseModel):
    aspect: str = "16:9"
    scenes: list[SceneSpec] = Field(default_factory=list)


class AssetCandidate(BaseModel):
    provider: str
    provider_asset_id: str
    kind: str
    source_url: str
    download_url: str
    author: str | None = None
    license: str
    width: int | None = None
    height: int | None = None
    duration_s: float | None = None


class AssetFile(BaseModel):
    candidate: AssetCandidate
    local_path: Path
    content_hash: str


class VoiceResult(BaseModel):
    audio_path: Path
    duration_s: float
    word_timings: list[dict[str, Any]] = Field(default_factory=list)
    provider: str
    voice: str


class TimelineItem(BaseModel):
    scene_position: int
    asset_path: Path
    start_s: float
    end_s: float
    effect: str | None = None


class Timeline(BaseModel):
    aspect: str = "16:9"
    items: list[TimelineItem] = Field(default_factory=list)
    narration_path: Path | None = None
    subtitle_path: Path | None = None
    music_path: Path | None = None


class RenderResult(BaseModel):
    output_path: Path
    duration_s: float
    width: int
    height: int


class QualityFinding(BaseModel):
    name: str
    status: str = Field(pattern=r"^(pass|warn|fail)$")
    score: float = Field(ge=0.0, le=100.0)
    detail: str = ""


class QualityReport(BaseModel):
    findings: list[QualityFinding] = Field(default_factory=list)
    total_score: float = Field(default=0.0, ge=0.0, le=100.0)
    passed: bool = False


# -------------------------------------------------------------- protocols


@runtime_checkable
class ResearchProvider(Protocol):
    name: str

    def search(
        self, query: str, *, language: str, context: SearchContext, limit: int = 10
    ) -> SearchResult: ...


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def generate(self, request: LLMRequest) -> LLMResponse: ...


@runtime_checkable
class FactChecker(Protocol):
    name: str

    def check(self, claims: list[ClaimInput]) -> FactCheckResult: ...


@runtime_checkable
class StoryboardGenerator(Protocol):
    name: str

    def generate(
        self, sections: list[str], *, aspect: str, target_duration_s: int
    ) -> StoryboardSpec: ...


@runtime_checkable
class AssetProvider(Protocol):
    name: str

    def search(
        self, terms: list[str], *, kind: str, aspect: str, limit: int = 10
    ) -> list[AssetCandidate]: ...

    def download(
        self, candidate: AssetCandidate, destination_dir: Path
    ) -> AssetFile: ...


@runtime_checkable
class VoiceProvider(Protocol):
    name: str

    def synthesize(
        self, text: str, *, voice: str, destination: Path, rate: float = 1.0
    ) -> VoiceResult: ...


@runtime_checkable
class Renderer(Protocol):
    name: str

    def render(self, timeline: Timeline, *, destination: Path) -> RenderResult: ...


@runtime_checkable
class QualityChecker(Protocol):
    name: str

    def check(self, render: RenderResult, timeline: Timeline) -> QualityReport: ...


# --------------------------------------------------------------- registry


class ProviderRegistry:
    """Tiny name → instance registry so modules stay interchangeable."""

    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}

    def register(self, kind: str, provider: Any) -> None:
        name = getattr(provider, "name", None)
        if not name:
            raise ValueError("provider must expose a non-empty 'name' attribute")
        self._items.setdefault(kind, {})[name] = provider

    def get(self, kind: str, name: str) -> Any:
        try:
            return self._items[kind][name]
        except KeyError as exc:
            raise KeyError(f"no {kind} provider registered as {name!r}") from exc

    def names(self, kind: str) -> list[str]:
        return sorted(self._items.get(kind, {}))
