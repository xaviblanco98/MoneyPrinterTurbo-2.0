"""Explicit, validated state machine for ``VideoProject``.

Only the transitions listed in ``TRANSITIONS`` are legal. A project that
fails records the state it failed from and can only be resumed to that state.
Every applied transition is appended to ``project_state_transitions``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from mpt2.errors import InvalidTransitionError
from mpt2.models import ProjectStateTransition, VideoProject, utcnow
from mpt2.models.enums import ProjectState as S

TRANSITIONS: dict[S, frozenset[S]] = {
    S.idea: frozenset({S.researching, S.rejected}),
    S.researching: frozenset({S.script_draft, S.failed}),
    S.script_draft: frozenset({S.fact_check, S.failed}),
    S.fact_check: frozenset({S.storyboard, S.script_draft, S.failed}),
    S.storyboard: frozenset({S.editorial_review, S.failed}),
    # Editorial package review (H2): humans approve to production or send back.
    S.editorial_review: frozenset(
        {
            S.assets,
            S.researching,
            S.script_draft,
            S.fact_check,
            S.storyboard,
            S.rejected,
            S.failed,
        }
    ),
    S.assets: frozenset({S.voice, S.failed}),
    S.voice: frozenset({S.rendering, S.failed}),
    S.rendering: frozenset({S.quality_control, S.failed}),
    # QC can send the project back to any production stage it found faulty.
    S.quality_control: frozenset(
        {
            S.awaiting_approval,
            S.script_draft,
            S.storyboard,
            S.assets,
            S.voice,
            S.rendering,
            S.failed,
        }
    ),
    S.awaiting_approval: frozenset({S.approved, S.rejected}),
    S.approved: frozenset({S.completed, S.failed}),
    S.rejected: frozenset({S.idea, S.script_draft}),
    S.completed: frozenset(),
    # ``failed`` may only resume to ``failed_from_state``; enforced in code.
    S.failed: frozenset(
        {
            S.researching,
            S.script_draft,
            S.fact_check,
            S.storyboard,
            S.assets,
            S.voice,
            S.rendering,
            S.quality_control,
            S.editorial_review,
            S.approved,
        }
    ),
}

TERMINAL_STATES = frozenset({S.completed})
WORK_STATES = frozenset(
    {
        S.researching,
        S.script_draft,
        S.fact_check,
        S.storyboard,
        S.assets,
        S.voice,
        S.rendering,
        S.quality_control,
    }
)


def allowed_transitions(project: VideoProject) -> frozenset[S]:
    current = S(project.state)
    targets = TRANSITIONS[current]
    if current is S.failed:
        if not project.failed_from_state:
            return frozenset()
        origin = S(project.failed_from_state)
        return frozenset({origin}) if origin in targets else frozenset()
    return targets


def can_transition(project: VideoProject, to_state: S | str) -> bool:
    try:
        target = S(to_state)
    except ValueError:
        return False
    return target in allowed_transitions(project)


def transition(
    session: Session,
    project: VideoProject,
    to_state: S | str,
    *,
    actor: str = "system",
    reason: str | None = None,
    now: datetime | None = None,
) -> ProjectStateTransition:
    """Apply a validated transition and record it. Raises on illegal moves."""
    try:
        target = S(to_state)
    except ValueError as exc:
        raise InvalidTransitionError(
            f"unknown state {to_state!r}", module=__name__
        ) from exc

    current = S(project.state)
    if target not in allowed_transitions(project):
        allowed = sorted(s.value for s in allowed_transitions(project))
        raise InvalidTransitionError(
            f"cannot move project {project.id} from {current.value!r} to {target.value!r}; "
            f"allowed: {allowed}",
            module=__name__,
        )

    moment = now or utcnow()
    if target is S.failed:
        project.failed_from_state = current.value
    elif current is S.failed:
        project.failed_from_state = None

    record = ProjectStateTransition(
        project_id=project.id,
        from_state=current.value,
        to_state=target.value,
        actor=actor,
        reason=reason,
        created_at=moment,
    )
    project.state = target
    project.state_updated_at = moment
    session.add(record)
    session.flush()
    return record
