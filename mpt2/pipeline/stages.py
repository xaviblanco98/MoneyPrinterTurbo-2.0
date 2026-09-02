"""Ordered editorial stages and the project state each one runs in."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from mpt2.editorial import angles, claims, qc, research, script, storyboard
from mpt2.models.enums import ProjectState as S


@dataclass(frozen=True)
class Stage:
    name: str
    state: S
    handler: Callable


STAGES: list[Stage] = [
    Stage("research_plan", S.researching, research.research_plan_stage),
    Stage("research_search", S.researching, research.research_search_stage),
    Stage("research_dossier", S.researching, research.research_dossier_stage),
    Stage("claims_extract", S.researching, claims.claims_extract_stage),
    Stage("claims_verify", S.researching, claims.claims_verify_stage),
    Stage("angles_hooks", S.script_draft, angles.angles_hooks_stage),
    Stage("script_write", S.script_draft, script.script_write_stage),
    Stage("script_fact_check", S.fact_check, script.script_fact_check_stage),
    Stage("storyboard", S.storyboard, storyboard.storyboard_stage),
    Stage("editorial_qc", S.storyboard, qc.editorial_qc_stage),
]
FINAL_STATE = S.editorial_review
STAGE_BY_NAME = {s.name: s for s in STAGES}
STAGE_NAMES = [s.name for s in STAGES]


def next_stage(name: str) -> Stage | None:
    idx = STAGE_NAMES.index(name)
    return STAGES[idx + 1] if idx + 1 < len(STAGES) else None


def first_stage_of_state(state: S) -> Stage | None:
    return next((s for s in STAGES if s.state == state), None)
