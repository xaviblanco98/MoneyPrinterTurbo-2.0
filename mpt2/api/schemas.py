"""Request and response models of the mpt2 HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mpt2.channels.schema import ChannelConfig
from mpt2.models.enums import (
    ApprovalDecision,
    ApprovalStage,
    CostUnit,
    JobStatus,
    ProjectState,
    VideoFormat,
)


class ChannelCreate(ChannelConfig):
    pass


class ChannelOut(ChannelConfig):
    model_config = ConfigDict(from_attributes=True)

    id: str
    active: bool
    created_at: datetime
    updated_at: datetime


class ProjectCreate(BaseModel):
    channel_id: str = Field(description="channel id or slug")
    title: str = Field(min_length=1, max_length=300)
    topic: str = Field(min_length=1)
    format: VideoFormat = VideoFormat.long
    language: str | None = None
    notes: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    channel_id: str
    title: str
    topic: str
    format: VideoFormat
    language: str
    state: ProjectState
    failed_from_state: str | None
    state_updated_at: datetime
    parent_project_id: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class TransitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_state: str
    to_state: str
    actor: str
    reason: str | None
    created_at: datetime


class ProjectStateOut(BaseModel):
    project_id: str
    state: ProjectState
    failed_from_state: str | None
    allowed_transitions: list[ProjectState]
    history: list[TransitionOut]


class TransitionRequest(BaseModel):
    to_state: ProjectState
    actor: str = Field(default="api", min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=2000)


class ApprovalRequest(BaseModel):
    stage: ApprovalStage
    decision: ApprovalDecision
    reviewer: str = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    stage: ApprovalStage
    decision: ApprovalDecision
    reviewer: str
    notes: str | None
    resulting_state: str | None
    created_at: datetime


class CostCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    stage: str = Field(min_length=1, max_length=32)
    units: float = Field(ge=0.0)
    unit_type: CostUnit
    est_cost_eur: float = Field(default=0.0, ge=0.0)
    note: str | None = None


class CostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str | None
    channel_id: str
    provider: str
    stage: str
    units: float
    unit_type: CostUnit
    est_cost_eur: float
    note: str | None
    created_at: datetime


class CostSummaryOut(BaseModel):
    project_id: str
    total_est_cost_eur: float
    entries: list[CostOut]


class ErrorOut(BaseModel):
    job_id: str
    stage: str
    status: JobStatus
    attempts: int
    max_attempts: int
    code: str | None
    message: str | None
    module: str | None
    occurred_at: datetime | None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    stage: str
    status: JobStatus
    attempts: int
    max_attempts: int
    next_run_at: datetime
    error_code: str | None
    error_message: str | None
    result: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class HealthOut(BaseModel):
    status: str
    version: str
    database: str
    schema_revision: str | None
    schema_head: str | None
    schema_current: bool
    warnings: list[str]
