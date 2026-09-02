"""Angles, hooks and narrative structures with scoring and selection."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mpt2.editorial import prompts
from mpt2.editorial.context import (
    PipelineContext,
    channel_dict,
    claim_dict,
    project_claims,
)
from mpt2.editorial.schemas import AnglesHooksOut, EditorialOptionOut
from mpt2.errors import StageError
from mpt2.models import Dossier, EditorialOption, PipelineJob, VideoProject
from mpt2.models.enums import OptionKind


def options_for(
    session: Session,
    project: VideoProject,
    kind: OptionKind | None = None,
    run: int | None = None,
) -> list[EditorialOption]:
    stmt = select(EditorialOption).where(
        EditorialOption.project_id == project.id,
        EditorialOption.run == (run or project.pipeline_run),
    )
    if kind:
        stmt = stmt.where(EditorialOption.kind == kind)
    return list(
        session.scalars(
            stmt.order_by(EditorialOption.kind, EditorialOption.position)
        ).all()
    )


def _store(
    session: Session,
    project: VideoProject,
    kind: OptionKind,
    items: list[EditorialOptionOut],
    recommended: int,
    valid_claim_ids: set[str],
) -> list[EditorialOption]:
    rows = []
    for position, item in enumerate(items):
        row = EditorialOption(
            project_id=project.id,
            run=project.pipeline_run,
            kind=kind,
            position=position,
            title=item.title.strip(),
            text=item.text.strip(),
            scores={
                **item.scores.model_dump(),
                "claim_ids": [c for c in item.claim_ids if c in valid_claim_ids],
            },
            total_score=item.scores.total(),
            rationale=item.rationale or None,
            selected=(position == recommended),
            selected_by="system" if position == recommended else None,
        )
        session.add(row)
        rows.append(row)
    if recommended >= len(items):  # model pointed outside the list: pick best score
        best = max(rows, key=lambda r: r.total_score)
        best.selected, best.selected_by = True, "system"
    return rows


def angles_hooks_stage(
    ctx: PipelineContext, job: PipelineJob, session: Session
) -> dict[str, Any]:
    project = session.get(VideoProject, job.project_id)
    if options_for(session, project):
        return {"skipped": True}
    claims = project_claims(session, project, usable_only=True)
    if len(claims) < 3:
        raise StageError(
            f"only {len(claims)} usable (supported/weak) claims; cannot build angles",
            code="insufficient_claims",
            module=__name__,
            retryable=False,
        )
    dossier = session.scalar(
        select(Dossier).where(
            Dossier.project_id == project.id, Dossier.run == project.pipeline_run
        )
    )
    summary = {
        "executive_summary": dossier.content.get("executive_summary")
        if dossier
        else "",
        "title_assessment": dossier.content.get("title_assessment") if dossier else "",
    }
    result = ctx.llm.call(
        "angles_hooks",
        prompts.angles_prompt(
            project.title,
            project.topic,
            channel_dict(project.channel),
            summary,
            [claim_dict(c, with_evidence=False) for c in claims],
        ),
        schema=AnglesHooksOut,
        system=prompts.ANGLES_SYSTEM,
        project_id=project.id,
        channel_id=project.channel_id,
        stage=job.stage,
        session=session,
        max_tokens=10000,
        metadata={"claim_ids": [c.id for c in claims]},
    )
    out: AnglesHooksOut = result.parsed
    valid = {c.id for c in claims}
    _store(session, project, OptionKind.angle, out.angles, out.recommended_angle, valid)
    _store(session, project, OptionKind.hook, out.hooks, out.recommended_hook, valid)
    _store(
        session,
        project,
        OptionKind.structure,
        out.structures,
        out.recommended_structure,
        valid,
    )
    for row in options_for(session, project):
        if row.selected and row.selected_by == "system":
            row.selection_reason = out.recommendation_rationale
    session.flush()
    return {
        "angles": len(out.angles),
        "hooks": len(out.hooks),
        "structures": len(out.structures),
        "cost_eur": result.cost_eur,
    }
