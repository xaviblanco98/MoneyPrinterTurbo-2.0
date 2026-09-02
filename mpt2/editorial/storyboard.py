"""Storyboard: scene plan per script section (no asset search yet)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mpt2.editorial import prompts
from mpt2.editorial.context import (
    WORDS_PER_SECOND,
    PipelineContext,
    channel_dict,
    claim_dict,
    est_seconds,
)
from mpt2.editorial.schemas import StoryboardSectionOut
from mpt2.editorial.script import current_script
from mpt2.errors import StageError
from mpt2.models import PipelineJob, ResearchClaim, Scene, Storyboard, VideoProject
from mpt2.models.enums import AssetType

ASSET_TYPE_MAP = {
    "stock": AssetType.stock_footage,
    "photo": AssetType.photo,
    "archive": AssetType.historical_archive,
    "chart": AssetType.chart,
    "table": AssetType.chart,
    "map": AssetType.map,
    "timeline": AssetType.timeline,
    "animated_text": AssetType.animated_text,
    "authorized_screenshot": AssetType.authorized_screenshot,
    "generated_image": AssetType.generated_image,
    "broll": AssetType.abstract_broll,
}


def current_storyboard(
    session: Session, project: VideoProject, run: int | None = None
) -> Storyboard | None:
    return session.scalar(
        select(Storyboard)
        .where(
            Storyboard.project_id == project.id,
            Storyboard.run == (run or project.pipeline_run),
        )
        .order_by(Storyboard.version.desc())
    )


def storyboard_stage(
    ctx: PipelineContext, job: PipelineJob, session: Session
) -> dict[str, Any]:
    project = session.get(VideoProject, job.project_id)
    script = current_script(session, project)
    if script is None:
        raise StageError(
            "script missing", code="missing_script", module=__name__, retryable=False
        )
    storyboard = current_storyboard(session, project)
    if storyboard is None:
        storyboard = Storyboard(
            project_id=project.id,
            run=project.pipeline_run,
            version=project.pipeline_run,
            script_id=script.id,
            aspect="16:9",
        )
        session.add(storyboard)
        session.flush()
    done_sections = {s.section_id for s in storyboard.scenes}
    channel = channel_dict(project.channel)
    cost = 0.0
    position = max((s.position for s in storyboard.scenes), default=-1) + 1
    created = 0
    for sec in script.sections:
        if sec.id in done_sections:
            continue
        claims = [
            c
            for c in (session.get(ResearchClaim, cid) for cid in sec.claim_ids)
            if c is not None
        ]
        result = ctx.llm.call(
            "storyboard",
            prompts.storyboard_prompt(
                {
                    "id": sec.id,
                    "role": getattr(sec.role, "value", sec.role),
                    "title": sec.title,
                    "text": sec.text,
                    "claim_ids": sec.claim_ids,
                },
                channel,
                [claim_dict(c, with_evidence=False) for c in claims],
                WORDS_PER_SECOND,
            ),
            schema=StoryboardSectionOut,
            system=prompts.STORYBOARD_SYSTEM,
            project_id=project.id,
            channel_id=project.channel_id,
            stage=job.stage,
            session=session,
            max_tokens=8000,
            metadata={
                "section_id": sec.id,
                "section_text": sec.text,
                "claim_ids": sec.claim_ids,
            },
        )
        cost += result.cost_eur
        scenes = result.parsed.scenes
        # Normalize durations to the section's narration length.
        raw_total = sum(s.est_duration_s for s in scenes) or 1.0
        target = max(sec.est_duration_s, 1.0)
        valid_claims = {c.id for c in claims}
        source_ids_by_claim = {c.id: [s.id for s in c.sources] for c in claims}
        for scene in scenes:
            claim_ids = [i for i in scene.claim_ids if i in valid_claims]
            source_ids = sorted(
                {sid for cid in claim_ids for sid in source_ids_by_claim.get(cid, [])}
            )
            duration = round(max(scene.est_duration_s * target / raw_total, 1.0), 1)
            session.add(
                Scene(
                    storyboard_id=storyboard.id,
                    section_id=sec.id,
                    position=position,
                    narration=scene.narration.strip(),
                    est_duration_s=duration
                    if abs(duration - est_seconds(scene.narration)) < 6
                    else est_seconds(scene.narration) or duration,
                    narrative_goal=scene.narrative_goal,
                    visual_description=scene.visual_description,
                    asset_type=ASSET_TYPE_MAP.get(
                        scene.asset_type, AssetType.stock_footage
                    ),
                    search_terms=scene.search_terms[:8],
                    on_screen_text=scene.on_screen_text or None,
                    chart_data=scene.chart_data.model_dump()
                    if scene.chart_data and scene.chart_data.chart_type != "none"
                    else None,
                    motion=scene.motion or None,
                    transition=scene.transition or "cut",
                    claim_ids=claim_ids,
                    source_ids=source_ids,
                    copyright_risk=scene.copyright_risk,
                    priority=scene.priority,
                    fallback_visual=scene.fallback_visual or None,
                    confidence=0.7,
                )
            )
            position += 1
            created += 1
        session.flush()
        session.commit()  # resumable per section
    session.refresh(storyboard)
    storyboard.total_est_duration_s = round(
        sum(s.est_duration_s for s in storyboard.scenes), 1
    )
    session.flush()
    return {
        "skipped": created == 0,
        "storyboard_id": storyboard.id,
        "scenes": len(storyboard.scenes),
        "created": created,
        "cost_eur": cost,
    }
