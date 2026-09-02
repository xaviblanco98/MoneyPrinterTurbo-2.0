"""SQLAlchemy models for mpt2 (milestone H1).

Every artifact hangs from ``VideoProject`` so the complete history of a video
can be reconstructed from its permanent id.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mpt2.models.base import (
    Base,
    EnumStr,
    IdMixin,
    TimestampMixin,
    UTCDateTime,
    new_id,
    utcnow,
)
from mpt2.models.enums import (
    ApprovalDecision,
    ApprovalStage,
    AssetKind,
    AssetLicense,
    AssetStatus,
    AssetType,
    ClaimImportance,
    ClaimKind,
    CostUnit,
    JobStatus,
    LLMCallStatus,
    OptionKind,
    ProjectState,
    ScriptStatus,
    SectionRole,
    SourceStatus,
    VerificationStatus,
    VideoFormat,
)

__all__ = [
    "Base",
    "Channel",
    "VideoProject",
    "ProjectStateTransition",
    "ResearchSource",
    "ResearchClaim",
    "research_claim_sources",
    "Script",
    "ScriptSection",
    "Storyboard",
    "Scene",
    "Asset",
    "PipelineJob",
    "QualityCheck",
    "HumanApproval",
    "CostEntry",
    "LLMCall",
    "LLMCache",
    "BudgetLimit",
    "ResearchPlan",
    "SearchQuery",
    "Dossier",
    "EditorialOption",
    "ReviewEvent",
    "new_id",
    "utcnow",
]


class Channel(IdMixin, TimestampMixin, Base):
    """Editorial configuration of one channel. Never stores secrets."""

    __tablename__ = "channels"

    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    niche: Mapped[str] = mapped_column(String(200), nullable=False)
    audience: Mapped[str] = mapped_column(Text, nullable=False)
    tone: Mapped[str] = mapped_column(Text, nullable=False)
    target_duration_s: Mapped[int] = mapped_column(Integer, nullable=False)
    voice: Mapped[str] = mapped_column(String(200), nullable=False)
    visual_style: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_sources: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    banned_sources: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    max_budget_eur: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    quality_threshold: Mapped[float] = mapped_column(
        Float, nullable=False, default=70.0
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    projects: Mapped[list["VideoProject"]] = relationship(back_populates="channel")


class VideoProject(IdMixin, TimestampMixin, Base):
    """The permanent identity of one video and the hub of all its artifacts."""

    __tablename__ = "video_projects"

    channel_id: Mapped[str] = mapped_column(
        ForeignKey("channels.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[VideoFormat] = mapped_column(
        EnumStr(VideoFormat, 16), nullable=False, default=VideoFormat.long
    )
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[ProjectState] = mapped_column(
        EnumStr(ProjectState, 32), nullable=False, default=ProjectState.idea, index=True
    )
    # State the project was in when it moved to ``failed``; the only state a
    # failed project may be resumed to.
    failed_from_state: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    state_updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    parent_project_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("video_projects.id"), nullable=True
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Incremented on every human-requested rerun; artifacts are versioned by run.
    pipeline_run: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    channel: Mapped[Channel] = relationship(back_populates="projects")
    transitions: Mapped[list["ProjectStateTransition"]] = relationship(
        back_populates="project", order_by="ProjectStateTransition.created_at"
    )
    jobs: Mapped[list["PipelineJob"]] = relationship(back_populates="project")
    approvals: Mapped[list["HumanApproval"]] = relationship(back_populates="project")
    costs: Mapped[list["CostEntry"]] = relationship(back_populates="project")


class ProjectStateTransition(IdMixin, Base):
    """Append-only history of state changes (who, why, when)."""

    __tablename__ = "project_state_transitions"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("video_projects.id"), nullable=False, index=True
    )
    from_state: Mapped[str] = mapped_column(String(32), nullable=False)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False, default="system")
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )

    project: Mapped[VideoProject] = relationship(back_populates="transitions")


research_claim_sources = Table(
    "research_claim_sources",
    Base.metadata,
    Column("claim_id", ForeignKey("research_claims.id"), primary_key=True),
    Column("source_id", ForeignKey("research_sources.id"), primary_key=True),
)


class ResearchSource(IdMixin, TimestampMixin, Base):
    __tablename__ = "research_sources"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("video_projects.id"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    publisher: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    license: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    reliability: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    excerpt_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    run: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    canonical_url: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, index=True
    )
    domain: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    accessed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    # Verbatim short fragments (<= ~150 chars each) returned as citations.
    snippets: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[SourceStatus] = mapped_column(
        EnumStr(SourceStatus, 16), nullable=False, default=SourceStatus.candidate
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    query_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("search_queries.id"), nullable=True
    )

    claims: Mapped[list["ResearchClaim"]] = relationship(
        secondary=research_claim_sources, back_populates="sources"
    )


class ResearchClaim(IdMixin, TimestampMixin, Base):
    __tablename__ = "research_claims"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("video_projects.id"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[ClaimKind] = mapped_column(
        EnumStr(ClaimKind, 16), nullable=False, default=ClaimKind.fact
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verification_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    contradicts_claim_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("research_claims.id"), nullable=True
    )
    entities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    run: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    importance: Mapped[ClaimImportance] = mapped_column(
        EnumStr(ClaimImportance, 16), nullable=False, default=ClaimImportance.medium
    )
    geographic_scope: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    time_period: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    # [{"source_id": ..., "text": ...}] verbatim evidence fragments
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    contradicting_source_ids: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    verification_status: Mapped[VerificationStatus] = mapped_column(
        EnumStr(VerificationStatus, 16),
        nullable=False,
        default=VerificationStatus.unverified,
    )
    used_in_script: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    human_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    discarded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    sources: Mapped[list[ResearchSource]] = relationship(
        secondary=research_claim_sources, back_populates="claims"
    )


class Script(IdMixin, TimestampMixin, Base):
    __tablename__ = "scripts"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_script_version"),
    )

    project_id: Mapped[str] = mapped_column(
        ForeignKey("video_projects.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[ScriptStatus] = mapped_column(
        EnumStr(ScriptStatus, 16), nullable=False, default=ScriptStatus.draft
    )
    est_duration_s: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reviewer_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    run: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    angle_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("editorial_options.id"), nullable=True
    )
    hook_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("editorial_options.id"), nullable=True
    )
    structure_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("editorial_options.id"), nullable=True
    )
    human_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    sections: Mapped[list["ScriptSection"]] = relationship(
        back_populates="script",
        order_by="ScriptSection.position",
        cascade="all, delete-orphan",
    )


class ScriptSection(IdMixin, Base):
    __tablename__ = "script_sections"
    __table_args__ = (
        UniqueConstraint("script_id", "position", name="uq_section_position"),
    )

    script_id: Mapped[str] = mapped_column(
        ForeignKey("scripts.id"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[SectionRole] = mapped_column(EnumStr(SectionRole, 16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    claim_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    needs_verification: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    est_duration_s: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    human_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    script: Mapped[Script] = relationship(back_populates="sections")


class Storyboard(IdMixin, TimestampMixin, Base):
    __tablename__ = "storyboards"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_storyboard_version"),
    )

    project_id: Mapped[str] = mapped_column(
        ForeignKey("video_projects.id"), nullable=False, index=True
    )
    script_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("scripts.id"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    aspect: Mapped[str] = mapped_column(String(8), nullable=False, default="16:9")
    total_est_duration_s: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    run: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    scenes: Mapped[list["Scene"]] = relationship(
        back_populates="storyboard",
        order_by="Scene.position",
        cascade="all, delete-orphan",
    )


class Scene(IdMixin, Base):
    __tablename__ = "scenes"
    __table_args__ = (
        UniqueConstraint("storyboard_id", "position", name="uq_scene_position"),
    )

    storyboard_id: Mapped[str] = mapped_column(
        ForeignKey("storyboards.id"), nullable=False, index=True
    )
    section_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("script_sections.id"), nullable=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    narration: Mapped[str] = mapped_column(Text, nullable=False)
    est_duration_s: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    narrative_goal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    visual_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    asset_type: Mapped[AssetType] = mapped_column(
        EnumStr(AssetType, 32), nullable=False, default=AssetType.stock_footage
    )
    search_terms: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    on_screen_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    animation: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    transition: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    framing: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    motion: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    license_requirement: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    fallback_visual: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    chart_data: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    claim_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    source_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    copyright_risk: Mapped[str] = mapped_column(
        String(16), nullable=False, default="low"
    )
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    human_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    storyboard: Mapped[Storyboard] = relationship(back_populates="scenes")
    assets: Mapped[list["Asset"]] = relationship(back_populates="scene")


class Asset(IdMixin, TimestampMixin, Base):
    """A media file with mandatory provenance and license."""

    __tablename__ = "assets"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("video_projects.id"), nullable=False, index=True
    )
    scene_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("scenes.id"), nullable=True
    )
    kind: Mapped[AssetKind] = mapped_column(EnumStr(AssetKind, 16), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_asset_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    author: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    license: Mapped[AssetLicense] = mapped_column(
        EnumStr(AssetLicense, 32), nullable=False, default=AssetLicense.unknown
    )
    attribution_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    local_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )
    relevance_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[AssetStatus] = mapped_column(
        EnumStr(AssetStatus, 16), nullable=False, default=AssetStatus.candidate
    )

    scene: Mapped[Optional[Scene]] = relationship(back_populates="assets")


class PipelineJob(IdMixin, TimestampMixin, Base):
    """A unit of work for one stage of one project, durable across restarts."""

    __tablename__ = "pipeline_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_job_idempotency_key"),
        Index("ix_jobs_status_next_run", "status", "next_run_at"),
    )

    project_id: Mapped[str] = mapped_column(
        ForeignKey("video_projects.id"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        EnumStr(JobStatus, 16), nullable=False, default=JobStatus.queued
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_run_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    locked_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    locked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_module: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    error_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)

    project: Mapped[VideoProject] = relationship(back_populates="jobs")


class QualityCheck(IdMixin, TimestampMixin, Base):
    __tablename__ = "quality_checks"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("video_projects.id"), nullable=False, index=True
    )
    checks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    total_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sent_back_to_state: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="technical")
    run: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class HumanApproval(IdMixin, Base):
    __tablename__ = "human_approvals"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("video_projects.id"), nullable=False, index=True
    )
    stage: Mapped[ApprovalStage] = mapped_column(
        EnumStr(ApprovalStage, 16), nullable=False
    )
    decision: Mapped[ApprovalDecision] = mapped_column(
        EnumStr(ApprovalDecision, 32), nullable=False
    )
    reviewer: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resulting_state: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )

    project: Mapped[VideoProject] = relationship(back_populates="approvals")


class CostEntry(IdMixin, Base):
    __tablename__ = "cost_entries"

    project_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("video_projects.id"), nullable=True, index=True
    )
    channel_id: Mapped[str] = mapped_column(
        ForeignKey("channels.id"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    units: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    unit_type: Mapped[CostUnit] = mapped_column(EnumStr(CostUnit, 16), nullable=False)
    est_cost_eur: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )

    project: Mapped[Optional[VideoProject]] = relationship(back_populates="costs")


class LLMCall(IdMixin, Base):
    """Telemetry for every model call, including cache hits and blocked calls.
    Prompts are never stored; only their hash and length."""

    __tablename__ = "llm_calls"

    project_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("video_projects.id"), nullable=True, index=True
    )
    channel_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("channels.id"), nullable=True
    )
    task: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    stage: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(200), nullable=False, index=True
    )
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    prompt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    web_search_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cost_eur: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[LLMCallStatus] = mapped_column(
        EnumStr(LLMCallStatus, 16), nullable=False, default=LLMCallStatus.ok
    )
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    stop_reason: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False, index=True
    )


class LLMCache(Base):
    """Exact-input response cache so identical calls are never paid twice."""

    __tablename__ = "llm_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    last_hit_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class BudgetLimit(Base):
    """Admin-editable safety limits (EUR). Absent rows fall back to settings."""

    __tablename__ = "budget_limits"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    value_eur: Mapped[float] = mapped_column(Float, nullable=False)
    updated_by: Mapped[str] = mapped_column(
        String(200), nullable=False, default="system"
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class ResearchPlan(IdMixin, TimestampMixin, Base):
    __tablename__ = "research_plans"
    __table_args__ = (
        UniqueConstraint("project_id", "run", name="uq_research_plan_run"),
    )

    project_id: Mapped[str] = mapped_column(
        ForeignKey("video_projects.id"), nullable=False, index=True
    )
    run: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class SearchQuery(IdMixin, Base):
    """Log of every search issued for a project: query, provider, results, errors."""

    __tablename__ = "search_queries"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("video_projects.id"), nullable=False, index=True
    )
    run: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    results: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    executed_queries: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    cost_eur: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class Dossier(IdMixin, TimestampMixin, Base):
    __tablename__ = "dossiers"
    __table_args__ = (UniqueConstraint("project_id", "run", name="uq_dossier_run"),)

    project_id: Mapped[str] = mapped_column(
        ForeignKey("video_projects.id"), nullable=False, index=True
    )
    run: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    researched_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class EditorialOption(IdMixin, TimestampMixin, Base):
    """Angles, hooks and narrative structures with scores and the selection."""

    __tablename__ = "editorial_options"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("video_projects.id"), nullable=False, index=True
    )
    run: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    kind: Mapped[OptionKind] = mapped_column(EnumStr(OptionKind, 16), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    scores: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    total_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    selected_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    selection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ReviewEvent(IdMixin, Base):
    """Audit trail of human edits and selections (actor, when, why, what changed)."""

    __tablename__ = "review_events"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("video_projects.id"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    changes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
