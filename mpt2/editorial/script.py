"""Script writing and script-level fact-check (phase 2)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mpt2.editorial import prompts
from mpt2.editorial.angles import options_for
from mpt2.editorial.context import (
    WORDS_PER_MINUTE,
    PipelineContext,
    channel_dict,
    claim_dict,
    est_seconds,
    project_claims,
    unsupported_numbers,
    word_count,
)
from mpt2.editorial.schemas import ScriptOut, ScriptRepairOut
from mpt2.errors import StageError
from mpt2.models import (
    Dossier,
    PipelineJob,
    QualityCheck,
    ResearchClaim,
    Script,
    ScriptSection,
    VideoProject,
)
from mpt2.models.enums import (
    ClaimImportance,
    OptionKind,
    ScriptStatus,
    SectionRole,
    VerificationStatus,
)

MIN_MINUTES, MAX_MINUTES = 6.0, 8.0


def current_script(
    session: Session, project: VideoProject, run: int | None = None
) -> Script | None:
    return session.scalar(
        select(Script)
        .where(
            Script.project_id == project.id, Script.run == (run or project.pipeline_run)
        )
        .order_by(Script.version.desc())
    )


def _selected(session: Session, project: VideoProject, kind: OptionKind):
    for opt in options_for(session, project, kind):
        if opt.selected:
            return opt
    raise StageError(
        f"no selected {kind.value}",
        code="missing_selection",
        module=__name__,
        retryable=False,
    )


def _option_payload(opt) -> dict[str, Any]:
    return {
        "id": opt.id,
        "title": opt.title,
        "text": opt.text,
        "claim_ids": opt.scores.get("claim_ids", []),
    }


def script_write_stage(
    ctx: PipelineContext, job: PipelineJob, session: Session
) -> dict[str, Any]:
    project = session.get(VideoProject, job.project_id)
    if current_script(session, project) is not None:
        return {"skipped": True}
    claims = project_claims(session, project, usable_only=True)
    if len(claims) < 3:
        raise StageError(
            "not enough usable claims to write a sourced script",
            code="insufficient_claims",
            module=__name__,
            retryable=False,
        )
    angle, hook, structure = (
        _selected(session, project, k)
        for k in (OptionKind.angle, OptionKind.hook, OptionKind.structure)
    )
    dossier = session.scalar(
        select(Dossier).where(
            Dossier.project_id == project.id, Dossier.run == project.pipeline_run
        )
    )
    channel = channel_dict(project.channel)
    target_words = int(channel["target_duration_s"] / 60.0 * WORDS_PER_MINUTE)
    result = ctx.llm.call(
        "script_write",
        prompts.script_prompt(
            project.title,
            channel,
            _option_payload(angle),
            _option_payload(hook),
            _option_payload(structure),
            [claim_dict(c, with_evidence=False) for c in claims],
            dossier.content if dossier else {},
            target_words,
        ),
        schema=ScriptOut,
        system=prompts.SCRIPT_SYSTEM,
        project_id=project.id,
        channel_id=project.channel_id,
        stage=job.stage,
        session=session,
        max_tokens=16000,
        metadata={
            "claims": [{"id": c.id, "text": c.text} for c in claims],
            "target_words": target_words,
        },
    )
    out: ScriptOut = result.parsed
    valid = {c.id: c for c in claims}
    script = Script(
        project_id=project.id,
        run=project.pipeline_run,
        version=project.pipeline_run,
        language=project.language,
        status=ScriptStatus.draft,
        title=out.title.strip(),
        angle_id=angle.id,
        hook_id=hook.id,
        structure_id=structure.id,
    )
    total_words = 0
    for position, sec in enumerate(out.sections):
        ids = [i for i in sec.claim_ids if i in valid]
        words = word_count(sec.text)
        total_words += words
        script.sections.append(
            ScriptSection(
                position=position,
                role=SectionRole(sec.role),
                title=sec.title.strip(),
                text=sec.text.strip(),
                claim_ids=ids,
                word_count=words,
                est_duration_s=est_seconds(sec.text),
                notes=sec.notes or None,
            )
        )
        for cid in ids:
            valid[cid].used_in_script = True
    script.word_count = total_words
    script.est_duration_s = round(total_words / WORDS_PER_MINUTE * 60.0, 1)
    session.add(script)
    session.flush()
    return {
        "script_id": script.id,
        "words": total_words,
        "est_minutes": round(script.est_duration_s / 60, 2),
        "cost_eur": result.cost_eur,
    }


# ---------------------------------------------------------- fact-check


def section_problems(session: Session, script: Script) -> list[dict[str, Any]]:
    """Automatic sourcing checks on every section. Pure data, no LLM."""
    problems: list[dict[str, Any]] = []
    for sec in script.sections:
        claims = [session.get(ResearchClaim, cid) for cid in sec.claim_ids]
        claims = [c for c in claims if c is not None and not c.discarded]
        missing = unsupported_numbers(sec.text, claims)
        bad_status = [
            c.id
            for c in claims
            if c.verification_status
            in (VerificationStatus.unsupported, VerificationStatus.unverified)
        ]
        if missing:
            problems.append(
                {
                    "section_id": sec.id,
                    "title": sec.title,
                    "type": "number_without_claim",
                    "detail": f"numbers not found in referenced claims/evidence: {missing}",
                    "numbers": missing,
                }
            )
        if bad_status:
            problems.append(
                {
                    "section_id": sec.id,
                    "title": sec.title,
                    "type": "unsupported_claim_reference",
                    "detail": f"references unsupported/unverified claims: {bad_status}",
                    "claim_ids": bad_status,
                }
            )
    return problems


def script_fact_check_stage(
    ctx: PipelineContext, job: PipelineJob, session: Session
) -> dict[str, Any]:
    project = session.get(VideoProject, job.project_id)
    script = current_script(session, project)
    if script is None:
        raise StageError(
            "script missing", code="missing_script", module=__name__, retryable=False
        )
    existing = session.scalar(
        select(QualityCheck).where(
            QualityCheck.project_id == project.id,
            QualityCheck.run == project.pipeline_run,
            QualityCheck.kind == "fact_check",
        )
    )
    if existing is not None and not script.human_edited:
        return {"skipped": True, "passed": existing.passed}
    problems = section_problems(session, script)
    cost = 0.0
    repaired = 0
    if problems:
        usable = project_claims(session, project, usable_only=True)
        by_title = {s.title: s for s in script.sections}
        offending = [
            s for s in script.sections if any(p["section_id"] == s.id for p in problems)
        ]
        result = ctx.llm.call(
            "script_repair",
            prompts.script_repair_prompt(
                [
                    {
                        "title": s.title,
                        "role": s.role.value if hasattr(s.role, "value") else s.role,
                        "text": s.text,
                        "claim_ids": s.claim_ids,
                    }
                    for s in offending
                ],
                problems,
                [claim_dict(c, with_evidence=True) for c in usable],
            ),
            schema=ScriptRepairOut,
            system=prompts.SCRIPT_SYSTEM,
            project_id=project.id,
            channel_id=project.channel_id,
            stage=job.stage,
            session=session,
            max_tokens=8000,
            metadata={
                "claims": [{"id": c.id, "text": c.text} for c in usable],
                "sections": [
                    {"title": s.title, "text": s.text, "claim_ids": s.claim_ids}
                    for s in offending
                ],
                "problems": problems,
            },
        )
        cost += result.cost_eur
        valid = {c.id: c for c in usable}
        for fixed in result.parsed.sections:
            sec = by_title.get(fixed.title)
            if sec is None:
                continue
            sec.text = fixed.text.strip()
            sec.claim_ids = [i for i in fixed.claim_ids if i in valid]
            sec.word_count = word_count(sec.text)
            sec.est_duration_s = est_seconds(sec.text)
            for cid in sec.claim_ids:
                valid[cid].used_in_script = True
            repaired += 1
        script.word_count = sum(s.word_count for s in script.sections)
        script.est_duration_s = round(script.word_count / WORDS_PER_MINUTE * 60.0, 1)
        problems = section_problems(session, script)
    for sec in script.sections:
        sec.needs_verification = any(p["section_id"] == sec.id for p in problems)
    critical_unsupported = [
        c.id
        for c in project_claims(session, project)
        if c.used_in_script
        and c.importance == ClaimImportance.critical
        and c.verification_status
        in (
            VerificationStatus.unsupported,
            VerificationStatus.unverified,
            VerificationStatus.disputed,
        )
    ]
    minutes = script.est_duration_s / 60.0
    checks = [
        {
            "name": "numbers_traceable",
            "status": "pass"
            if not [p for p in problems if p["type"] == "number_without_claim"]
            else "fail",
            "detail": f"{len([p for p in problems if p['type'] == 'number_without_claim'])} sections with untraced numbers",
            "kind": "automatic",
        },
        {
            "name": "no_unsupported_claim_references",
            "status": "pass"
            if not [p for p in problems if p["type"] == "unsupported_claim_reference"]
            else "fail",
            "detail": f"{len([p for p in problems if p['type'] == 'unsupported_claim_reference'])} sections referencing unsupported claims",
            "kind": "automatic",
        },
        {
            "name": "critical_claims_supported",
            "status": "pass" if not critical_unsupported else "fail",
            "detail": f"critical claims used without support: {critical_unsupported}",
            "kind": "automatic",
        },
        {
            "name": "duration_in_range",
            "status": "pass" if MIN_MINUTES <= minutes <= MAX_MINUTES else "warn",
            "detail": f"estimated {minutes:.1f} min (target {MIN_MINUTES:.0f}-{MAX_MINUTES:.0f})",
            "kind": "automatic",
        },
    ]
    passed = all(
        c["status"] == "pass" for c in checks if c["name"] != "duration_in_range"
    )
    if existing is not None:
        existing.checks, existing.passed, existing.total_score = (
            checks,
            passed,
            100.0 if passed else 0.0,
        )
        existing.sent_back_to_state = None if passed else "script_draft"
    else:
        session.add(
            QualityCheck(
                project_id=project.id,
                run=project.pipeline_run,
                kind="fact_check",
                checks=checks,
                passed=passed,
                total_score=100.0 if passed else 0.0,
                sent_back_to_state=None if passed else "script_draft",
            )
        )
    script.human_edited = False
    session.flush()
    return {
        "passed": passed,
        "problems": len(problems),
        "repaired_sections": repaired,
        "critical_unsupported": critical_unsupported,
        "cost_eur": cost,
    }
