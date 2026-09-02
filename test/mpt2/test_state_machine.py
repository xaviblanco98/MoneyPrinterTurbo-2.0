from __future__ import annotations

import pytest

from mpt2 import services
from mpt2.errors import InvalidTransitionError
from mpt2.models.enums import ProjectState as S
from mpt2.state_machine import (
    TRANSITIONS,
    allowed_transitions,
    can_transition,
    transition,
)

HAPPY_PATH = [
    S.researching,
    S.script_draft,
    S.fact_check,
    S.storyboard,
    S.editorial_review,
    S.assets,
    S.voice,
    S.rendering,
    S.quality_control,
    S.awaiting_approval,
    S.approved,
    S.completed,
]


@pytest.fixture
def project(session, channel_config):
    channel = services.create_channel(session, channel_config)
    return services.create_project(session, channel, title="T", topic="topic")


def test_every_state_has_a_rule():
    assert set(TRANSITIONS) == set(S)


def test_happy_path_and_history(session, project):
    for state in HAPPY_PATH:
        transition(session, project, state, actor="test", reason=f"to {state.value}")
    session.commit()
    assert project.state == S.completed
    assert allowed_transitions(project) == frozenset()
    history = [(t.from_state, t.to_state) for t in project.transitions]
    assert history[0] == ("idea", "researching")
    assert history[-1] == ("approved", "completed")
    assert len(history) == len(HAPPY_PATH)


@pytest.mark.parametrize(
    "start,target",
    [
        (S.idea, S.storyboard),
        (S.idea, S.completed),
        (S.idea, S.approved),
        (S.researching, S.rendering),
        (S.script_draft, S.awaiting_approval),
        (S.awaiting_approval, S.completed),
        (S.completed, S.idea),
        (S.rendering, S.idea),
        (S.storyboard, S.assets),
        (S.editorial_review, S.completed),
        (S.editorial_review, S.approved),
    ],
)
def test_invalid_jumps_rejected(session, project, start, target):
    project.state = start
    with pytest.raises(InvalidTransitionError) as exc:
        transition(session, project, target)
    assert "allowed" in exc.value.message
    assert project.state == start
    assert project.transitions == []


def test_unknown_state_rejected(session, project):
    with pytest.raises(InvalidTransitionError, match="unknown state"):
        transition(session, project, "publishing")
    assert not can_transition(project, "publishing")


def test_failed_resumes_only_to_origin(session, project):
    transition(session, project, S.researching)
    transition(session, project, S.script_draft)
    transition(session, project, S.failed, reason="llm down")
    assert project.failed_from_state == "script_draft"
    assert allowed_transitions(project) == frozenset({S.script_draft})
    with pytest.raises(InvalidTransitionError):
        transition(session, project, S.researching)
    transition(session, project, S.script_draft, reason="retry")
    assert project.failed_from_state is None
    assert project.state == S.script_draft


def test_failed_without_origin_is_stuck(session, project):
    project.state = S.failed
    project.failed_from_state = None
    assert allowed_transitions(project) == frozenset()


def test_quality_control_can_send_back(session, project):
    project.state = S.quality_control
    transition(session, project, S.assets, reason="irrelevant footage")
    assert project.state == S.assets


def test_rejected_can_be_reopened(session, project):
    project.state = S.awaiting_approval
    transition(session, project, S.rejected)
    assert can_transition(project, S.idea) and can_transition(project, S.script_draft)
    assert not can_transition(project, S.approved)
