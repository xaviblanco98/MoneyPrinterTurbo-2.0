"""Application services: the only place that writes channels, projects,
approvals and costs. Used by the API and the CLI so both behave identically."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mpt2.channels.schema import ChannelConfig
from mpt2.errors import NotFoundError
from mpt2.models import Channel, CostEntry, HumanApproval, PipelineJob, VideoProject
from mpt2.models.enums import (
    ApprovalDecision,
    ApprovalStage,
    CostUnit,
    JobStatus,
    ProjectState,
    VideoFormat,
)
from mpt2.state_machine import transition

# ------------------------------------------------------------- channels


def create_channel(session: Session, config: ChannelConfig) -> Channel:
    """Create a channel from a validated config. Idempotent on slug."""
    existing = session.scalar(select(Channel).where(Channel.slug == config.slug))
    if existing is not None:
        return existing
    channel = Channel(**config.model_dump())
    session.add(channel)
    session.flush()
    return channel


def upsert_channel(session: Session, config: ChannelConfig) -> Channel:
    """Create the channel or update its editorial fields when the slug exists."""
    channel = session.scalar(select(Channel).where(Channel.slug == config.slug))
    if channel is None:
        return create_channel(session, config)
    for key, value in config.model_dump().items():
        setattr(channel, key, value)
    session.flush()
    return channel


def list_channels(session: Session) -> list[Channel]:
    return list(session.scalars(select(Channel).order_by(Channel.created_at)).all())


def get_channel(session: Session, channel_id: str) -> Channel:
    channel = session.get(Channel, channel_id)
    if channel is None:
        channel = session.scalar(select(Channel).where(Channel.slug == channel_id))
    if channel is None:
        raise NotFoundError(f"channel {channel_id!r} not found", module=__name__)
    return channel


# ------------------------------------------------------------- projects


def create_project(
    session: Session,
    channel: Channel,
    *,
    title: str,
    topic: str,
    format: VideoFormat | str = VideoFormat.long,
    language: str | None = None,
    notes: str | None = None,
) -> VideoProject:
    project = VideoProject(
        channel_id=channel.id,
        title=title.strip(),
        topic=topic.strip(),
        format=VideoFormat(format),
        language=language or channel.language,
        state=ProjectState.idea,
        notes=notes,
    )
    session.add(project)
    session.flush()
    return project


def get_project(session: Session, project_id: str) -> VideoProject:
    project = session.get(VideoProject, project_id)
    if project is None:
        raise NotFoundError(f"project {project_id!r} not found", module=__name__)
    return project


def list_projects(
    session: Session, *, channel_id: str | None = None
) -> list[VideoProject]:
    stmt = select(VideoProject).order_by(VideoProject.created_at.desc())
    if channel_id:
        stmt = stmt.where(VideoProject.channel_id == channel_id)
    return list(session.scalars(stmt).all())


def advance_project(
    session: Session,
    project: VideoProject,
    to_state: ProjectState | str,
    *,
    actor: str = "api",
    reason: str | None = None,
) -> VideoProject:
    transition(session, project, to_state, actor=actor, reason=reason)
    return project


# ------------------------------------------------------------ approvals


def record_approval(
    session: Session,
    project: VideoProject,
    *,
    stage: ApprovalStage | str,
    decision: ApprovalDecision | str,
    reviewer: str,
    notes: str | None = None,
) -> HumanApproval:
    """Persist a human decision. A decision on the ``final`` stage of a project
    that is ``awaiting_approval`` also moves the project (approved/rejected)."""
    stage = ApprovalStage(stage)
    decision = ApprovalDecision(decision)
    resulting_state: str | None = None
    if stage is ApprovalStage.final and project.state == ProjectState.awaiting_approval:
        target = {
            ApprovalDecision.approve: ProjectState.approved,
            ApprovalDecision.reject: ProjectState.rejected,
            ApprovalDecision.changes_requested: ProjectState.rejected,
        }[decision]
        transition(
            session,
            project,
            target,
            actor=f"human:{reviewer}",
            reason=notes or f"{decision.value} at {stage.value}",
        )
        resulting_state = target.value
    approval = HumanApproval(
        project_id=project.id,
        stage=stage,
        decision=decision,
        reviewer=reviewer.strip(),
        notes=notes,
        resulting_state=resulting_state,
    )
    session.add(approval)
    session.flush()
    return approval


# ---------------------------------------------------------------- costs


def record_cost(
    session: Session,
    *,
    channel_id: str,
    provider: str,
    stage: str,
    units: float,
    unit_type: CostUnit | str,
    est_cost_eur: float = 0.0,
    project_id: str | None = None,
    note: str | None = None,
) -> CostEntry:
    entry = CostEntry(
        project_id=project_id,
        channel_id=channel_id,
        provider=provider,
        stage=stage,
        units=float(units),
        unit_type=CostUnit(unit_type),
        est_cost_eur=float(est_cost_eur),
        note=note,
    )
    session.add(entry)
    session.flush()
    return entry


def project_costs(session: Session, project_id: str) -> tuple[list[CostEntry], float]:
    entries = list(
        session.scalars(
            select(CostEntry)
            .where(CostEntry.project_id == project_id)
            .order_by(CostEntry.created_at)
        ).all()
    )
    total = session.scalar(
        select(func.coalesce(func.sum(CostEntry.est_cost_eur), 0.0)).where(
            CostEntry.project_id == project_id
        )
    )
    return entries, float(total or 0.0)


# --------------------------------------------------------------- errors


def project_errors(session: Session, project_id: str) -> list[dict[str, Any]]:
    """Jobs of the project that recorded an error, newest first."""
    jobs = session.scalars(
        select(PipelineJob)
        .where(
            PipelineJob.project_id == project_id, PipelineJob.error_code.is_not(None)
        )
        .order_by(PipelineJob.error_at.desc())
    ).all()
    return [
        {
            "job_id": job.id,
            "stage": job.stage,
            "status": JobStatus(job.status).value,
            "attempts": job.attempts,
            "max_attempts": job.max_attempts,
            "code": job.error_code,
            "message": job.error_message,
            "module": job.error_module,
            "occurred_at": job.error_at,
        }
        for job in jobs
    ]
