from __future__ import annotations

from pathlib import Path

import pytest

from mpt2 import contracts as c
from mpt2.errors import StageError
from mpt2.providers.llm import UpstreamLLMProvider
from mpt2.settings import Settings


class FakeLLM:
    name = "fake"

    def generate(self, request: c.LLMRequest) -> c.LLMResponse:
        return c.LLMResponse(
            text=f"echo:{request.prompt}", provider=self.name, model="m"
        )


class FakeResearch:
    name = "fake-research"

    def search(
        self, query: str, *, language: str, context: c.SearchContext, limit: int = 10
    ) -> c.SearchResult:
        return c.SearchResult(
            query=query,
            provider=self.name,
            candidates=[c.SourceCandidate(url="https://example.org", title=query)],
        )


class FakeFactChecker:
    name = "fake-fc"

    def check(self, claims):
        return c.FactCheckResult(
            findings=[
                c.FactCheckFinding(
                    claim_id=x.claim_id, status="block", reason="no source"
                )
                for x in claims
                if not x.source_urls
            ]
        )


class FakeStoryboard:
    name = "fake-sb"

    def generate(self, sections, *, aspect, target_duration_s):
        return c.StoryboardSpec(
            aspect=aspect,
            scenes=[
                c.SceneSpec(position=i, narration=s, est_duration_s=5)
                for i, s in enumerate(sections)
            ],
        )


class FakeAssets:
    name = "fake-assets"

    def search(self, terms, *, kind, aspect, limit=10):
        return []

    def download(self, candidate, destination_dir):
        raise NotImplementedError


class FakeVoice:
    name = "fake-voice"

    def synthesize(self, text, *, voice, destination, rate=1.0):
        return c.VoiceResult(
            audio_path=destination, duration_s=1.0, provider=self.name, voice=voice
        )


class FakeRenderer:
    name = "fake-render"

    def render(self, timeline, *, destination):
        return c.RenderResult(
            output_path=destination, duration_s=1.0, width=1920, height=1080
        )


class FakeQC:
    name = "fake-qc"

    def check(self, render, timeline):
        return c.QualityReport(findings=[], total_score=100, passed=True)


@pytest.mark.parametrize(
    "impl,protocol",
    [
        (FakeLLM(), c.LLMProvider),
        (FakeResearch(), c.ResearchProvider),
        (FakeFactChecker(), c.FactChecker),
        (FakeStoryboard(), c.StoryboardGenerator),
        (FakeAssets(), c.AssetProvider),
        (FakeVoice(), c.VoiceProvider),
        (FakeRenderer(), c.Renderer),
        (FakeQC(), c.QualityChecker),
    ],
)
def test_fakes_satisfy_protocols(impl, protocol):
    assert isinstance(impl, protocol)


def test_object_without_methods_does_not_satisfy():
    assert not isinstance(object(), c.LLMProvider)


def test_fact_check_result_blocked_property():
    result = FakeFactChecker().check(
        [
            c.ClaimInput(claim_id="1", text="x"),
            c.ClaimInput(claim_id="2", text="y", source_urls=["u"]),
        ]
    )
    assert result.blocked and [f.claim_id for f in result.findings] == ["1"]


def test_payload_validation():
    with pytest.raises(ValueError):
        c.FactCheckFinding(claim_id="1", status="maybe", reason="r")
    with pytest.raises(ValueError):
        c.LLMRequest(prompt="")
    with pytest.raises(ValueError):
        c.QualityFinding(name="n", status="pass", score=120)


def test_registry_roundtrip():
    registry = c.ProviderRegistry()
    registry.register("llm", FakeLLM())
    registry.register("voice", FakeVoice())
    assert (
        registry.get("llm", "fake").generate(c.LLMRequest(prompt="hi")).text
        == "echo:hi"
    )
    assert registry.names("llm") == ["fake"] and registry.names("none") == []
    with pytest.raises(KeyError):
        registry.get("llm", "missing")
    with pytest.raises(ValueError):
        registry.register("llm", object())


def test_upstream_llm_adapter_builds_ollama_config_from_env():
    settings = Settings.from_env(
        {
            "MPT2_OLLAMA_MODEL": "qwen2.5:14b",
            "MPT2_OLLAMA_BASE_URL": "http://gpu-box:11434/v1",
        },
        env_file=None,
    )
    captured = {}

    def backend(prompt, app_config):
        captured["prompt"], captured["config"] = prompt, app_config
        return "Some script"

    provider = UpstreamLLMProvider(settings, backend=backend)
    assert isinstance(provider, c.LLMProvider)
    response = provider.generate(c.LLMRequest(prompt="Write", system="You are terse."))
    assert (
        response.text == "Some script"
        and response.provider == "ollama"
        and response.model == "qwen2.5:14b"
    )
    assert captured["prompt"] == "You are terse.\n\nWrite"
    assert captured["config"] == {
        "llm_provider": "ollama",
        "ollama_base_url": "http://gpu-box:11434/v1",
        "ollama_model_name": "qwen2.5:14b",
    }


def test_upstream_llm_adapter_other_provider_and_errors():
    settings = Settings.from_env(
        {
            "MPT2_LLM_PROVIDER": "groq",
            "MPT2_LLM_API_KEY": "k",
            "MPT2_LLM_MODEL": "llama-3.1-8b-instant",
        },
        env_file=None,
    )
    provider = UpstreamLLMProvider(
        settings, backend=lambda p, cfg: "Error: quota exceeded"
    )
    assert provider.app_config()["groq_api_key"] == "k"
    with pytest.raises(StageError) as exc:
        provider.generate(c.LLMRequest(prompt="x"))
    assert exc.value.code == "llm_error" and "quota" in exc.value.message


def test_upstream_llm_adapter_uses_real_upstream_registry(monkeypatch):
    """The default backend reaches upstream llm._generate_response with our snapshot."""
    from app.services import llm as upstream

    monkeypatch.setattr(
        upstream,
        "_generate_response",
        lambda prompt, app_config=None: f"cfg:{app_config['llm_provider']}",
    )
    provider = UpstreamLLMProvider(Settings.from_env({}, env_file=None))
    assert provider.generate(c.LLMRequest(prompt="x")).text == "cfg:ollama"


def test_timeline_models_accept_paths(tmp_path: Path):
    timeline = c.Timeline(
        items=[
            c.TimelineItem(
                scene_position=0, asset_path=tmp_path / "a.mp4", start_s=0, end_s=5
            )
        ]
    )
    assert timeline.items[0].asset_path.name == "a.mp4"
