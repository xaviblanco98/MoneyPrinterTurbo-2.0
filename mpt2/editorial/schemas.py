"""Structured outputs the editorial agents request from the model.

Every LLM call in H2 returns one of these Pydantic models; the client
validates them and repairs once on failure. Keep them strict but not brittle:
the model produces text, the code decides.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ------------------------------------------------------------ research plan


class SearchStrategyItem(BaseModel):
    query: str = Field(min_length=3, max_length=300)
    purpose: str = Field(min_length=3, max_length=500)
    subquestion: str = Field(default="", max_length=300)
    perspective: str = Field(default="neutral", max_length=100)


class ResearchPlanOut(BaseModel):
    central_question: str = Field(min_length=10)
    subquestions: list[str] = Field(min_length=3, max_length=20)
    required_information: list[str] = Field(min_length=3, max_length=30)
    ideal_sources: list[str] = Field(min_length=2, max_length=20)
    misinformation_risks: list[str] = Field(default_factory=list, max_length=20)
    sensitive_claims: list[str] = Field(default_factory=list, max_length=20)
    search_strategy: list[SearchStrategyItem] = Field(min_length=4, max_length=40)
    hypotheses_to_test: list[str] = Field(default_factory=list, max_length=15)
    title_could_be_wrong_if: list[str] = Field(default_factory=list, max_length=10)


# --------------------------------------------------------------- dossier


class DossierFact(BaseModel):
    statement: str = Field(min_length=5)
    source_ids: list[str] = Field(min_length=1)
    geography: str = Field(default="", max_length=120)
    period: str = Field(default="", max_length=120)


class DossierFigure(BaseModel):
    label: str = Field(min_length=2, max_length=200)
    value: str = Field(min_length=1, max_length=120)
    unit: str = Field(default="", max_length=60)
    geography: str = Field(default="", max_length=120)
    period: str = Field(default="", max_length=120)
    source_ids: list[str] = Field(min_length=1)


class TimelineEntry(BaseModel):
    date: str = Field(min_length=2, max_length=60)
    event: str = Field(min_length=3)
    source_ids: list[str] = Field(default_factory=list)


class Contradiction(BaseModel):
    topic: str = Field(min_length=3)
    position_a: str
    source_ids_a: list[str] = Field(default_factory=list)
    position_b: str
    source_ids_b: list[str] = Field(default_factory=list)
    assessment: str = ""


class DossierOut(BaseModel):
    executive_summary: str = Field(min_length=50)
    key_facts: list[DossierFact] = Field(min_length=3)
    timeline: list[TimelineEntry] = Field(default_factory=list)
    figures: list[DossierFigure] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    discarded_hypotheses: list[str] = Field(default_factory=list)
    title_assessment: str = Field(
        default="",
        description="Does the evidence support, qualify or refute the working title?",
    )


# ---------------------------------------------------------------- claims


class ClaimEvidence(BaseModel):
    source_id: str
    text: str = Field(min_length=3, max_length=600)


class ClaimOut(BaseModel):
    text: str = Field(min_length=5, max_length=600)
    kind: Literal["fact", "estimate", "opinion", "inference"]
    importance: Literal["critical", "high", "medium", "low"]
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_source_ids: list[str] = Field(default_factory=list)
    contradicting_source_ids: list[str] = Field(default_factory=list)
    evidence: list[ClaimEvidence] = Field(default_factory=list)
    geographic_scope: str = Field(default="", max_length=120)
    time_period: str = Field(default="", max_length=120)
    entities: list[str] = Field(default_factory=list)
    notes: str = Field(default="", max_length=600)


class ClaimsOut(BaseModel):
    claims: list[ClaimOut] = Field(min_length=5, max_length=80)


class FactCheckVerdict(BaseModel):
    claim_id: str
    status: Literal["supported", "weak", "disputed", "unsupported"]
    reason: str = Field(min_length=3, max_length=600)


class FactCheckOut(BaseModel):
    verdicts: list[FactCheckVerdict]
    general_notes: str = ""


# --------------------------------------------------------- angles & hooks

SCORE_KEYS = (
    "curiosity",
    "clarity",
    "credibility",
    "specificity",
    "originality",
    "retention",
    "evidence_availability",
    "clickbait_risk",
    "legal_risk",
    "channel_fit",
)


class OptionScores(BaseModel):
    curiosity: float = Field(ge=0, le=10)
    clarity: float = Field(ge=0, le=10)
    credibility: float = Field(ge=0, le=10)
    specificity: float = Field(ge=0, le=10)
    originality: float = Field(ge=0, le=10)
    retention: float = Field(ge=0, le=10)
    evidence_availability: float = Field(ge=0, le=10)
    clickbait_risk: float = Field(ge=0, le=10, description="10 = very risky")
    legal_risk: float = Field(ge=0, le=10, description="10 = very risky")
    channel_fit: float = Field(ge=0, le=10)

    def total(self) -> float:
        positive = (
            self.curiosity
            + self.clarity
            + self.credibility
            + self.specificity
            + self.originality
            + self.retention
            + self.evidence_availability
            + self.channel_fit
        )
        return round(positive - self.clickbait_risk - self.legal_risk, 2)


class EditorialOptionOut(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    text: str = Field(min_length=10, max_length=1500)
    scores: OptionScores
    rationale: str = Field(default="", max_length=800)
    claim_ids: list[str] = Field(default_factory=list)


class AnglesHooksOut(BaseModel):
    angles: list[EditorialOptionOut] = Field(min_length=5, max_length=8)
    hooks: list[EditorialOptionOut] = Field(min_length=10, max_length=14)
    structures: list[EditorialOptionOut] = Field(min_length=3, max_length=5)
    recommended_angle: int = Field(ge=0)
    recommended_hook: int = Field(ge=0)
    recommended_structure: int = Field(ge=0)
    recommendation_rationale: str = Field(min_length=10)


# ---------------------------------------------------------------- script


class ScriptSectionOut(BaseModel):
    role: Literal[
        "hook", "promise", "context", "development", "reveal", "conclusion", "cta"
    ]
    title: str = Field(min_length=2, max_length=200)
    text: str = Field(min_length=10)
    claim_ids: list[str] = Field(default_factory=list)
    notes: str = Field(default="", max_length=600)


class ScriptOut(BaseModel):
    title: str = Field(min_length=5, max_length=200)
    sections: list[ScriptSectionOut] = Field(min_length=5, max_length=30)
    geography_note: str = Field(default="", max_length=800)


class ScriptRepairOut(BaseModel):
    sections: list[ScriptSectionOut] = Field(min_length=1)


# ------------------------------------------------------------ storyboard

ASSET_TYPES = (
    "stock",
    "photo",
    "archive",
    "chart",
    "table",
    "map",
    "timeline",
    "animated_text",
    "authorized_screenshot",
    "generated_image",
    "broll",
)


class ChartData(BaseModel):
    chart_type: Literal[
        "bar", "line", "pie", "table", "number", "comparison", "none"
    ] = "none"
    title: str = Field(default="", max_length=200)
    series: list[dict[str, str | float | int]] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)


class SceneOut(BaseModel):
    narration: str = Field(min_length=3)
    est_duration_s: float = Field(ge=1.0, le=30.0)
    narrative_goal: str = Field(min_length=3, max_length=300)
    visual_description: str = Field(min_length=10, max_length=800)
    asset_type: Literal[
        "stock",
        "photo",
        "archive",
        "chart",
        "table",
        "map",
        "timeline",
        "animated_text",
        "authorized_screenshot",
        "generated_image",
        "broll",
    ]
    search_terms: list[str] = Field(default_factory=list, max_length=8)
    on_screen_text: str = Field(default="", max_length=200)
    chart_data: ChartData | None = None
    motion: str = Field(default="", max_length=120)
    transition: str = Field(default="cut", max_length=60)
    claim_ids: list[str] = Field(default_factory=list)
    copyright_risk: Literal["low", "medium", "high"] = "low"
    fallback_visual: str = Field(default="", max_length=400)
    priority: Literal["must", "normal", "optional"] = "normal"


class StoryboardSectionOut(BaseModel):
    scenes: list[SceneOut] = Field(min_length=1, max_length=40)


# ------------------------------------------------------------ editorial QC


class QCSubjectiveScore(BaseModel):
    name: Literal[
        "hook_quality",
        "clarity",
        "estimated_retention",
        "originality",
        "legal_risk",
        "copyright_risk",
        "policy_risk",
        "storyboard_coherence",
        "source_quality",
    ]
    score: float = Field(ge=0, le=100)
    rationale: str = Field(min_length=3, max_length=800)


class EditorialQCOut(BaseModel):
    scores: list[QCSubjectiveScore] = Field(min_length=5)
    editorial_issues: list[str] = Field(default_factory=list, max_length=30)
    strengths: list[str] = Field(default_factory=list, max_length=20)
