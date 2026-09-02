"""Human review operations: edit claims, choose options, edit script/scenes,
approve or reject. Every action is recorded in ``review_events`` with actor,
time and reason. The editorial package can only be approved when no critical
claim used in the script lacks support and no section needs verification."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from mpt2.editorial.angles import options_for
from mpt2.editorial.context import est_seconds, project_claims, word_count
from mpt2.editorial.script import current_script
from mpt2.editorial.storyboard import current_storyboard
from mpt2.errors import InvalidTransitionError, NotFoundError
from mpt2.models import (
    EditorialOption,
    HumanApproval,
    ResearchClaim,
    ReviewEvent,
    Scene,
    ScriptSection,
    VideoProject,
)
from mpt2.models.enums import (
    ApprovalDecision,
    ApprovalStage,
    ClaimImportance,
    OptionKind,
    ProjectState as S,
    VerificationStatus,
)
from mpt2.state_machine import transition

EDITABLE_CLAIM_FIELDS = {
    "text",
    "kind",
    "importance",
    "confidence",
    "verification_status",
    "geographic_scope",
    "time_period",
    "notes",
    "discarded",
}
EDITABLE_SECTION_FIELDS = {"text", "title", "role", "claim_ids", "notes"}
EDITABLE_SCENE_FIELDS = {
    "narration",
    "est_duration_s",
    "narrative_goal",
    "visual_description",
    "asset_type",
    "search_terms",
    "on_screen_text",
    "chart_data",
    "motion",
    "transition",
    "claim_ids",
    "source_ids",
    "copyright_risk",
    "fallback_visual",
    "priority",
}


def _event(
    session: Session,
    project_id: str,
    entity_type: str,
    entity_id: str,
    action: str,
    actor: str,
    reason: str | None,
    changes: dict[str, Any],
) -> ReviewEvent:
    ev = ReviewEvent(
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor=actor,
        reason=reason,
        changes=changes,
    )
    session.add(ev)
    session.flush()
    return ev


def _apply(obj: Any, updates: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for key, value in updates.items():
        if key not in allowed or value is None:
            continue
        old = getattr(obj, key)
        old_v = getattr(old, "value", old)
        if old_v != value:
            setattr(obj, key, value)
            changes[key] = {"from": old_v, "to": value}
    return changes


def edit_claim(
    session: Session,
    claim_id: str,
    updates: dict[str, Any],
    *,
    actor: str,
    reason: str | None = None,
) -> ResearchClaim:
    claim = session.get(ResearchClaim, claim_id)
    if claim is None:
        raise NotFoundError(f"claim {claim_id!r} not found", module=__name__)
    changes = _apply(claim, updates, EDITABLE_CLAIM_FIELDS)
    if changes:
        claim.human_edited = True
        if "verification_status" in changes:
            claim.verified = claim.verification_status == VerificationStatus.supported
        _event(
            session,
            claim.project_id,
            "claim",
            claim.id,
            "discard" if updates.get("discarded") else "edit",
            actor,
            reason,
            changes,
        )
        # A script that uses an edited claim must be re-checked before approval.
        project = session.get(VideoProject, claim.project_id)
        script = current_script(session, project)
        if script and any(claim.id in s.claim_ids for s in script.sections):
            script.human_edited = True
    session.flush()
    return claim


def select_option(
    session: Session, option_id: str, *, actor: str, reason: str | None = None
) -> EditorialOption:
    option = session.get(EditorialOption, option_id)
    if option is None:
        raise NotFoundError(f"option {option_id!r} not found", module=__name__)
    project = session.get(VideoProject, option.project_id)
    for sibling in options_for(
        session, project, OptionKind(option.kind), run=option.run
    ):
        if sibling.selected and sibling.id != option.id:
            sibling.selected, sibling.selected_by = False, None
    option.selected, option.selected_by, option.selection_reason = (
        True,
        f"human:{actor}",
        reason,
    )
    _event(
        session,
        project.id,
        "option",
        option.id,
        "select",
        actor,
        reason,
        {"kind": option.kind.value, "title": option.title},
    )
    session.flush()
    return option


def edit_section(
    session: Session,
    section_id: str,
    updates: dict[str, Any],
    *,
    actor: str,
    reason: str | None = None,
) -> ScriptSection:
    section = session.get(ScriptSection, section_id)
    if section is None:
        raise NotFoundError(f"section {section_id!r} not found", module=__name__)
    if "claim_ids" in updates and updates["claim_ids"] is not None:
        valid = (
            {
                c.id
                for c in session.query(ResearchClaim)
                .filter(ResearchClaim.id.in_(updates["claim_ids"]))
                .all()
            }
            if updates["claim_ids"]
            else set()
        )
        updates["claim_ids"] = [c for c in updates["claim_ids"] if c in valid]
    changes = _apply(section, updates, EDITABLE_SECTION_FIELDS)
    if changes:
        section.human_edited = True
        section.word_count = word_count(section.text)
        section.est_duration_s = est_seconds(section.text)
        script = section.script
        script.human_edited = True
        script.word_count = sum(s.word_count for s in script.sections)
        script.est_duration_s = round(script.word_count / 150.0 * 60.0, 1)
        _event(
            session,
            script.project_id,
            "section",
            section.id,
            "edit",
            actor,
            reason,
            changes,
        )
    session.flush()
    return section


def edit_scene(
    session: Session,
    scene_id: str,
    updates: dict[str, Any],
    *,
    actor: str,
    reason: str | None = None,
) -> Scene:
    scene = session.get(Scene, scene_id)
    if scene is None:
        raise NotFoundError(f"scene {scene_id!r} not found", module=__name__)
    changes = _apply(scene, updates, EDITABLE_SCENE_FIELDS)
    if changes:
        scene.human_edited = True
        project_id = scene.storyboard.project_id
        _event(session, project_id, "scene", scene.id, "edit", actor, reason, changes)
        storyboard = scene.storyboard
        storyboard.total_est_duration_s = round(
            sum(s.est_duration_s for s in storyboard.scenes), 1
        )
    session.flush()
    return scene


def package_blockers(session: Session, project: VideoProject) -> list[str]:
    """Reasons the editorial package cannot be approved yet."""
    blockers: list[str] = []
    script = current_script(session, project)
    if script is None:
        blockers.append("no script")
    else:
        flagged = [s.title for s in script.sections if s.needs_verification]
        if flagged:
            blockers.append(f"sections needing verification: {flagged}")
        if script.human_edited:
            blockers.append(
                "script edited after the last fact-check; rerun script_fact_check"
            )
        used = {cid for s in script.sections for cid in s.claim_ids}
        for claim in project_claims(session, project):
            if (
                claim.id in used
                and claim.importance == ClaimImportance.critical
                and claim.verification_status
                not in (VerificationStatus.supported, VerificationStatus.weak)
            ):
                blockers.append(
                    f"critical claim {claim.id[:8]} used in script is {claim.verification_status.value}"
                )
    if current_storyboard(session, project) is None:
        blockers.append("no storyboard")
    return blockers


def record_review_decision(
    session: Session,
    project: VideoProject,
    *,
    stage: ApprovalStage | str,
    decision: ApprovalDecision | str,
    reviewer: str,
    notes: str | None = None,
) -> HumanApproval:
    """Approvals in the editorial review state. ``package`` decisions move the project."""
    stage = ApprovalStage(stage)
    decision = ApprovalDecision(decision)
    resulting: str | None = None
    if (
        stage in (ApprovalStage.package, ApprovalStage.final)
        and S(project.state) is not S.editorial_review
    ):
        raise InvalidTransitionError(
            f"package decisions require state editorial_review (project is {project.state.value!r})",
            module=__name__,
        )
    if stage in (ApprovalStage.package, ApprovalStage.final):
        if decision is ApprovalDecision.approve:
            blockers = package_blockers(session, project)
            if blockers:
                raise InvalidTransitionError(
                    "editorial package cannot be approved: " + "; ".join(blockers),
                    module=__name__,
                )
            transition(
                session,
                project,
                S.assets,
                actor=f"human:{reviewer}",
                reason=notes or "editorial package approved",
            )
            resulting = S.assets.value
        elif decision is ApprovalDecision.reject:
            transition(
                session,
                project,
                S.rejected,
                actor=f"human:{reviewer}",
                reason=notes or "editorial package rejected",
            )
            resulting = S.rejected.value
        else:  # changes requested: stay in review, the human then edits or reruns
            resulting = S.editorial_review.value
    approval = HumanApproval(
        project_id=project.id,
        stage=stage,
        decision=decision,
        reviewer=reviewer,
        notes=notes,
        resulting_state=resulting,
    )
    session.add(approval)
    _event(
        session,
        project.id,
        "project",
        project.id,
        f"{stage.value}:{decision.value}",
        reviewer,
        notes,
        {"resulting_state": resulting},
    )
    session.flush()
    return approval
