"""Editorial quality control: automatic tests + LLM subjective evaluation."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mpt2.editorial import prompts
from mpt2.editorial.angles import options_for
from mpt2.editorial.context import (
    PipelineContext,
    accepted_sources,
    numeric_tokens,
    project_claims,
    word_count,
)
from mpt2.editorial.schemas import EditorialQCOut
from mpt2.editorial.script import (
    MAX_MINUTES,
    MIN_MINUTES,
    current_script,
    section_problems,
)
from mpt2.editorial.storyboard import current_storyboard
from mpt2.errors import StageError
from mpt2.models import PipelineJob, QualityCheck, ResearchSource, VideoProject
from mpt2.models.enums import ClaimImportance, OptionKind, VerificationStatus
from mpt2.research.urls import is_banned

AUTOMATIC_WEIGHT, LLM_WEIGHT = 0.5, 0.5


def automatic_checks(session: Session, project: VideoProject) -> list[dict[str, Any]]:
    script = current_script(session, project)
    storyboard = current_storyboard(session, project)
    sources = accepted_sources(session, project)
    all_sources = session.scalars(
        select(ResearchSource).where(
            ResearchSource.project_id == project.id,
            ResearchSource.run == project.pipeline_run,
        )
    ).all()
    claims = project_claims(session, project)
    channel = project.channel
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, score: float, detail: str, warn: bool = False) -> None:
        checks.append(
            {
                "name": name,
                "status": "pass" if ok else ("warn" if warn else "fail"),
                "score": round(score, 1),
                "detail": detail,
                "kind": "automatic",
            }
        )

    primary = sum(1 for s in sources if s.is_primary)
    add(
        "source_count",
        len(sources) >= 5,
        min(100.0, len(sources) * 10.0),
        f"{len(sources)} accepted sources ({primary} primary)",
    )
    add(
        "primary_source_ratio",
        primary >= 2,
        min(100.0, primary / max(len(sources), 1) * 200),
        f"{primary} primary of {len(sources)}",
        warn=True,
    )
    banned_used = [
        s.domain
        for s in all_sources
        if s.status.value == "accepted"
        and is_banned(s.domain or "", channel.banned_sources or [])
    ]
    add(
        "no_banned_domains",
        not banned_used,
        100.0 if not banned_used else 0.0,
        f"banned domains in accepted sources: {banned_used}",
    )

    supported = sum(
        1 for c in claims if c.verification_status == VerificationStatus.supported
    )
    usable = sum(
        1
        for c in claims
        if c.verification_status
        in (VerificationStatus.supported, VerificationStatus.weak)
    )
    add(
        "claims_verified_ratio",
        usable >= max(5, int(len(claims) * 0.5)),
        usable / max(len(claims), 1) * 100,
        f"{supported} supported, {usable} usable of {len(claims)} claims",
        warn=True,
    )
    critical_bad = [
        c.id
        for c in claims
        if c.used_in_script
        and c.importance == ClaimImportance.critical
        and c.verification_status
        not in (VerificationStatus.supported, VerificationStatus.weak)
    ]
    add(
        "critical_claims_backed",
        not critical_bad,
        100.0 if not critical_bad else 0.0,
        f"critical claims used without support: {critical_bad}",
    )

    if script is None:
        add("script_present", False, 0.0, "no script")
    else:
        problems = section_problems(session, script)
        untraced = [p for p in problems if p["type"] == "number_without_claim"]
        sections_with_numbers = [s for s in script.sections if numeric_tokens(s.text)]
        traced = len(sections_with_numbers) - len({p["section_id"] for p in untraced})
        add(
            "traceability_numbers",
            not untraced,
            traced / max(len(sections_with_numbers), 1) * 100,
            f"{traced}/{len(sections_with_numbers)} sections with numbers fully traced to claims",
        )
        linked = sum(1 for s in script.sections if s.claim_ids)
        add(
            "traceability_sections",
            linked >= len(script.sections) * 0.6,
            linked / max(len(script.sections), 1) * 100,
            f"{linked}/{len(script.sections)} sections cite claims",
            warn=True,
        )
        minutes = script.est_duration_s / 60
        add(
            "duration_in_range",
            MIN_MINUTES <= minutes <= MAX_MINUTES,
            100.0 if MIN_MINUTES <= minutes <= MAX_MINUTES else 50.0,
            f"{minutes:.1f} min, {script.word_count} words",
            warn=True,
        )
        hook = next(
            (s for s in script.sections if getattr(s.role, "value", s.role) == "hook"),
            None,
        )
        add(
            "hook_present_and_short",
            hook is not None and word_count(hook.text) <= 90,
            100.0 if hook and word_count(hook.text) <= 90 else 40.0,
            f"hook words: {word_count(hook.text) if hook else 0}",
            warn=True,
        )
    if storyboard is None or not storyboard.scenes:
        add("storyboard_present", False, 0.0, "no scenes")
    else:
        scenes = storyboard.scenes
        section_ids = {s.id for s in script.sections} if script else set()
        covered = {s.section_id for s in scenes}
        add(
            "storyboard_covers_sections",
            section_ids <= covered,
            len(covered & section_ids) / max(len(section_ids), 1) * 100,
            f"{len(covered & section_ids)}/{len(section_ids)} sections have scenes",
        )
        in_range = sum(1 for s in scenes if 2.0 <= s.est_duration_s <= 12.0)
        add(
            "scene_durations",
            in_range >= len(scenes) * 0.8,
            in_range / len(scenes) * 100,
            f"{in_range}/{len(scenes)} scenes between 2 and 12 s",
            warn=True,
        )
        narration_words = sum(word_count(s.narration) for s in scenes)
        script_words = script.word_count if script else 0
        ratio = narration_words / max(script_words, 1)
        add(
            "storyboard_narration_coverage",
            0.85 <= ratio <= 1.15,
            max(0.0, 100 - abs(1 - ratio) * 200),
            f"scene narration words / script words = {ratio:.2f}",
            warn=True,
        )
        with_claims = sum(1 for s in scenes if s.claim_ids)
        add(
            "scenes_linked_to_claims",
            True,
            with_claims / len(scenes) * 100,
            f"{with_claims}/{len(scenes)} scenes reference claims",
            warn=True,
        )
        high_risk = sum(1 for s in scenes if s.copyright_risk == "high")
        add(
            "copyright_high_risk_scenes",
            high_risk <= len(scenes) * 0.1,
            100.0 - high_risk / len(scenes) * 100,
            f"{high_risk} high-risk scenes",
            warn=True,
        )
    return checks


def editorial_qc_stage(
    ctx: PipelineContext, job: PipelineJob, session: Session
) -> dict[str, Any]:
    project = session.get(VideoProject, job.project_id)
    existing = session.scalar(
        select(QualityCheck).where(
            QualityCheck.project_id == project.id,
            QualityCheck.run == project.pipeline_run,
            QualityCheck.kind == "editorial",
        )
    )
    if existing is not None:
        return {
            "skipped": True,
            "passed": existing.passed,
            "total_score": existing.total_score,
        }
    script = current_script(session, project)
    storyboard = current_storyboard(session, project)
    if script is None or storyboard is None:
        raise StageError(
            "script or storyboard missing",
            code="missing_artifacts",
            module=__name__,
            retryable=False,
        )
    checks = automatic_checks(session, project)
    hook = next(
        (o for o in options_for(session, project, OptionKind.hook) if o.selected), None
    )
    package = {
        "title": script.title,
        "hook": hook.text if hook else "",
        "sections": [
            {
                "role": getattr(s.role, "value", s.role),
                "title": s.title,
                "text": s.text,
                "claim_ids": s.claim_ids,
            }
            for s in script.sections
        ],
        "scenes_sample": [
            {
                "position": s.position,
                "narration": s.narration[:160],
                "visual": s.visual_description[:200],
                "asset_type": getattr(s.asset_type, "value", s.asset_type),
                "copyright_risk": s.copyright_risk,
            }
            for s in storyboard.scenes[:40]
        ],
        "sources": [
            {"domain": s.domain, "title": s.title, "primary": s.is_primary}
            for s in accepted_sources(session, project)
        ],
        "automatic_checks": checks,
    }
    result = ctx.llm.call(
        "editorial_qc",
        prompts.qc_prompt(package),
        schema=EditorialQCOut,
        system=prompts.QC_SYSTEM,
        project_id=project.id,
        channel_id=project.channel_id,
        stage=job.stage,
        session=session,
        max_tokens=6000,
    )
    out: EditorialQCOut = result.parsed
    llm_checks = [
        {
            "name": s.name,
            "status": "pass" if s.score >= 60 else "warn",
            "score": s.score,
            "detail": s.rationale,
            "kind": "llm",
        }
        for s in out.scores
    ]
    auto_score = sum(c["score"] for c in checks) / max(len(checks), 1)
    llm_score = sum(c["score"] for c in llm_checks) / max(len(llm_checks), 1)
    total = round(AUTOMATIC_WEIGHT * auto_score + LLM_WEIGHT * llm_score, 1)
    hard_fail = [c["name"] for c in checks if c["status"] == "fail"]
    passed = not hard_fail and total >= float(project.channel.quality_threshold)
    all_checks = (
        checks
        + llm_checks
        + [
            {
                "name": "editorial_issues",
                "status": "info",
                "score": 0,
                "detail": "; ".join(out.editorial_issues),
                "kind": "llm",
            },
            {
                "name": "strengths",
                "status": "info",
                "score": 0,
                "detail": "; ".join(out.strengths),
                "kind": "llm",
            },
        ]
    )
    session.add(
        QualityCheck(
            project_id=project.id,
            run=project.pipeline_run,
            kind="editorial",
            checks=all_checks,
            total_score=total,
            passed=passed,
            sent_back_to_state=None if passed else "editorial_review",
        )
    )
    session.flush()
    return {
        "passed": passed,
        "total_score": total,
        "hard_fail": hard_fail,
        "cost_eur": result.cost_eur,
    }
