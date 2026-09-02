"""HTTP routes for the H2 editorial pipeline and human review."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from mpt2 import services
from mpt2.editorial import review
from mpt2.editorial.angles import options_for
from mpt2.editorial.artifacts import ARTIFACT_FILES, cost_report, export_project
from mpt2.editorial.context import claim_dict, project_claims, source_dict
from mpt2.editorial.script import current_script
from mpt2.editorial.storyboard import current_storyboard
from mpt2.models import (
    Dossier,
    LLMCall,
    QualityCheck,
    ResearchPlan,
    ResearchSource,
    ReviewEvent,
    SearchQuery,
)
from mpt2.models.enums import ApprovalDecision, ApprovalStage, ProjectState


class RunRequest(BaseModel):
    actor: str = Field(default="api", max_length=200)
    reason: str | None = Field(default=None, max_length=2000)
    run_worker: bool = Field(
        default=False, description="process pending jobs synchronously (tests/CLI)"
    )
    max_jobs: int = Field(default=200, ge=1, le=1000)


class RerunRequest(RunRequest):
    stage: str


class ClaimEdit(BaseModel):
    actor: str = Field(min_length=1, max_length=200)
    reason: str | None = None
    text: str | None = None
    kind: str | None = None
    importance: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    verification_status: str | None = None
    geographic_scope: str | None = None
    time_period: str | None = None
    notes: str | None = None
    discarded: bool | None = None


class SelectRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=200)
    reason: str | None = None


class SectionEdit(BaseModel):
    actor: str = Field(min_length=1, max_length=200)
    reason: str | None = None
    text: str | None = None
    title: str | None = None
    role: str | None = None
    claim_ids: list[str] | None = None
    notes: str | None = None


class SceneEdit(BaseModel):
    actor: str = Field(min_length=1, max_length=200)
    reason: str | None = None
    narration: str | None = None
    est_duration_s: float | None = Field(default=None, ge=0.5, le=60)
    narrative_goal: str | None = None
    visual_description: str | None = None
    asset_type: str | None = None
    search_terms: list[str] | None = None
    on_screen_text: str | None = None
    chart_data: dict[str, Any] | None = None
    motion: str | None = None
    transition: str | None = None
    claim_ids: list[str] | None = None
    copyright_risk: str | None = None
    fallback_visual: str | None = None
    priority: str | None = None


class ReviewDecision(BaseModel):
    stage: ApprovalStage
    decision: ApprovalDecision
    reviewer: str = Field(min_length=1, max_length=200)
    notes: str | None = None


class BudgetUpdate(BaseModel):
    actor: str = Field(min_length=1, max_length=200)
    note: str | None = None
    warn_eur: float | None = Field(default=None, gt=0)
    monthly_hard_eur: float | None = Field(default=None, gt=0)
    project_eur: float | None = Field(default=None, gt=0)
    per_call_eur: float | None = Field(default=None, gt=0)


def build_router(
    get_db, pipeline_getter, worker_getter, budget_getter, settings
) -> APIRouter:
    router = APIRouter(prefix="/api/v2")

    def _run_worker(body: RunRequest) -> int:
        return (
            worker_getter().run_until_idle(max_jobs=body.max_jobs)
            if body.run_worker
            else 0
        )

    @router.post("/projects/{project_id}/run")
    def run_project(
        project_id: str, body: RunRequest, session: Session = Depends(get_db)
    ):
        project = services.get_project(session, project_id)
        pipeline = pipeline_getter()
        if (
            ProjectState(project.state) in (ProjectState.idea, ProjectState.researching)
            and not project.jobs
        ):
            job = pipeline.start(session, project, actor=body.actor, reason=body.reason)
        else:
            job = pipeline.resume(
                session, project, actor=body.actor, reason=body.reason
            )
        session.commit()
        ran = _run_worker(body)
        session.refresh(project)
        return {
            "project_id": project.id,
            "job_id": job.id,
            "stage": job.stage,
            "state": project.state,
            "jobs_run": ran,
            "run": project.pipeline_run,
        }

    @router.post("/projects/{project_id}/rerun")
    def rerun_project(
        project_id: str, body: RerunRequest, session: Session = Depends(get_db)
    ):
        project = services.get_project(session, project_id)
        job = pipeline_getter().rerun_from(
            session, project, body.stage, actor=body.actor, reason=body.reason
        )
        session.commit()
        ran = _run_worker(body)
        session.refresh(project)
        return {
            "project_id": project.id,
            "job_id": job.id,
            "stage": job.stage,
            "state": project.state,
            "jobs_run": ran,
            "run": project.pipeline_run,
        }

    @router.post("/worker/run")
    def worker_run(max_jobs: int = 100):
        return {"jobs_run": worker_getter().run_until_idle(max_jobs=max_jobs)}

    # ------------------------------------------------------------ reads
    @router.get("/projects/{project_id}/research-plan")
    def get_plan(project_id: str, session: Session = Depends(get_db)):
        project = services.get_project(session, project_id)
        plan = session.scalar(
            select(ResearchPlan).where(
                ResearchPlan.project_id == project.id,
                ResearchPlan.run == project.pipeline_run,
            )
        )
        return {"run": project.pipeline_run, "plan": plan.content if plan else None}

    @router.get("/projects/{project_id}/search-log")
    def get_search_log(project_id: str, session: Session = Depends(get_db)):
        project = services.get_project(session, project_id)
        rows = session.scalars(
            select(SearchQuery)
            .where(
                SearchQuery.project_id == project.id,
                SearchQuery.run == project.pipeline_run,
            )
            .order_by(SearchQuery.created_at)
        ).all()
        return [
            {
                "id": q.id,
                "query": q.query,
                "purpose": q.purpose,
                "status": q.status,
                "error": q.error,
                "result_count": q.result_count,
                "cost_eur": q.cost_eur,
                "created_at": q.created_at,
            }
            for q in rows
        ]

    @router.get("/projects/{project_id}/sources")
    def get_sources(project_id: str, session: Session = Depends(get_db)):
        project = services.get_project(session, project_id)
        rows = session.scalars(
            select(ResearchSource)
            .where(
                ResearchSource.project_id == project.id,
                ResearchSource.run == project.pipeline_run,
            )
            .order_by(ResearchSource.is_primary.desc(), ResearchSource.created_at)
        ).all()
        return [
            {
                **source_dict(s),
                "status": s.status.value,
                "rejection_reason": s.rejection_reason,
                "accessed_at": s.accessed_at,
            }
            for s in rows
        ]

    @router.get("/projects/{project_id}/dossier")
    def get_dossier(project_id: str, session: Session = Depends(get_db)):
        project = services.get_project(session, project_id)
        d = session.scalar(
            select(Dossier).where(
                Dossier.project_id == project.id, Dossier.run == project.pipeline_run
            )
        )
        return {
            "run": project.pipeline_run,
            "researched_at": d.researched_at if d else None,
            "dossier": d.content if d else None,
        }

    @router.get("/projects/{project_id}/claims")
    def get_claims(
        project_id: str,
        include_discarded: bool = False,
        session: Session = Depends(get_db),
    ):
        project = services.get_project(session, project_id)
        claims = project_claims(session, project)
        if include_discarded:
            from mpt2.models import ResearchClaim

            claims = list(
                session.scalars(
                    select(ResearchClaim).where(
                        ResearchClaim.project_id == project.id,
                        ResearchClaim.run == project.pipeline_run,
                    )
                ).all()
            )
        return [
            {
                **claim_dict(c),
                "used_in_script": c.used_in_script,
                "human_edited": c.human_edited,
                "discarded": c.discarded,
                "verification_note": c.verification_note,
                "notes": c.notes,
            }
            for c in claims
        ]

    @router.patch("/claims/{claim_id}")
    def patch_claim(claim_id: str, body: ClaimEdit, session: Session = Depends(get_db)):
        updates = body.model_dump(exclude={"actor", "reason"}, exclude_none=True)
        claim = review.edit_claim(
            session, claim_id, updates, actor=body.actor, reason=body.reason
        )
        return {
            **claim_dict(claim),
            "discarded": claim.discarded,
            "human_edited": claim.human_edited,
        }

    @router.get("/projects/{project_id}/options")
    def get_options(project_id: str, session: Session = Depends(get_db)):
        project = services.get_project(session, project_id)
        return [
            {
                "id": o.id,
                "kind": o.kind.value,
                "position": o.position,
                "title": o.title,
                "text": o.text,
                "scores": o.scores,
                "total_score": o.total_score,
                "rationale": o.rationale,
                "selected": o.selected,
                "selected_by": o.selected_by,
                "selection_reason": o.selection_reason,
            }
            for o in options_for(session, project)
        ]

    @router.post("/options/{option_id}/select")
    def select_option(
        option_id: str, body: SelectRequest, session: Session = Depends(get_db)
    ):
        o = review.select_option(
            session, option_id, actor=body.actor, reason=body.reason
        )
        return {
            "id": o.id,
            "kind": o.kind.value,
            "selected": o.selected,
            "selected_by": o.selected_by,
        }

    @router.get("/projects/{project_id}/script")
    def get_script(project_id: str, session: Session = Depends(get_db)):
        project = services.get_project(session, project_id)
        s = current_script(session, project)
        if s is None:
            return None
        return {
            "id": s.id,
            "title": s.title,
            "status": s.status.value,
            "word_count": s.word_count,
            "est_duration_s": s.est_duration_s,
            "human_edited": s.human_edited,
            "angle_id": s.angle_id,
            "hook_id": s.hook_id,
            "structure_id": s.structure_id,
            "sections": [
                {
                    "id": x.id,
                    "position": x.position,
                    "role": getattr(x.role, "value", x.role),
                    "title": x.title,
                    "text": x.text,
                    "claim_ids": x.claim_ids,
                    "word_count": x.word_count,
                    "est_duration_s": x.est_duration_s,
                    "needs_verification": x.needs_verification,
                    "human_edited": x.human_edited,
                    "notes": x.notes,
                }
                for x in s.sections
            ],
        }

    @router.patch("/sections/{section_id}")
    def patch_section(
        section_id: str, body: SectionEdit, session: Session = Depends(get_db)
    ):
        updates = body.model_dump(exclude={"actor", "reason"}, exclude_none=True)
        x = review.edit_section(
            session, section_id, updates, actor=body.actor, reason=body.reason
        )
        return {
            "id": x.id,
            "text": x.text,
            "claim_ids": x.claim_ids,
            "word_count": x.word_count,
            "human_edited": x.human_edited,
        }

    @router.get("/projects/{project_id}/storyboard")
    def get_storyboard(project_id: str, session: Session = Depends(get_db)):
        project = services.get_project(session, project_id)
        sb = current_storyboard(session, project)
        if sb is None:
            return None
        return {
            "id": sb.id,
            "aspect": sb.aspect,
            "total_est_duration_s": sb.total_est_duration_s,
            "scenes": [
                {
                    "id": s.id,
                    "position": s.position,
                    "section_id": s.section_id,
                    "narration": s.narration,
                    "est_duration_s": s.est_duration_s,
                    "narrative_goal": s.narrative_goal,
                    "visual_description": s.visual_description,
                    "asset_type": getattr(s.asset_type, "value", s.asset_type),
                    "search_terms": s.search_terms,
                    "on_screen_text": s.on_screen_text,
                    "chart_data": s.chart_data,
                    "motion": s.motion,
                    "transition": s.transition,
                    "claim_ids": s.claim_ids,
                    "source_ids": s.source_ids,
                    "copyright_risk": s.copyright_risk,
                    "fallback_visual": s.fallback_visual,
                    "priority": s.priority,
                    "human_edited": s.human_edited,
                }
                for s in sb.scenes
            ],
        }

    @router.patch("/scenes/{scene_id}")
    def patch_scene(scene_id: str, body: SceneEdit, session: Session = Depends(get_db)):
        updates = body.model_dump(exclude={"actor", "reason"}, exclude_none=True)
        s = review.edit_scene(
            session, scene_id, updates, actor=body.actor, reason=body.reason
        )
        return {
            "id": s.id,
            "narration": s.narration,
            "est_duration_s": s.est_duration_s,
            "human_edited": s.human_edited,
        }

    @router.get("/projects/{project_id}/quality")
    def get_quality(project_id: str, session: Session = Depends(get_db)):
        project = services.get_project(session, project_id)
        rows = session.scalars(
            select(QualityCheck)
            .where(
                QualityCheck.project_id == project.id,
                QualityCheck.run == project.pipeline_run,
            )
            .order_by(QualityCheck.created_at)
        ).all()
        return [
            {
                "id": q.id,
                "kind": q.kind,
                "passed": q.passed,
                "total_score": q.total_score,
                "checks": q.checks,
                "created_at": q.created_at,
            }
            for q in rows
        ]

    @router.get("/projects/{project_id}/review")
    def get_review(project_id: str, session: Session = Depends(get_db)):
        project = services.get_project(session, project_id)
        events = session.scalars(
            select(ReviewEvent)
            .where(ReviewEvent.project_id == project.id)
            .order_by(ReviewEvent.created_at)
        ).all()
        return {
            "state": project.state,
            "run": project.pipeline_run,
            "blockers": review.package_blockers(session, project),
            "events": [
                {
                    "entity_type": e.entity_type,
                    "entity_id": e.entity_id,
                    "action": e.action,
                    "actor": e.actor,
                    "reason": e.reason,
                    "changes": e.changes,
                    "created_at": e.created_at,
                }
                for e in events
            ],
        }

    @router.post("/projects/{project_id}/review", status_code=201)
    def post_review(
        project_id: str, body: ReviewDecision, session: Session = Depends(get_db)
    ):
        project = services.get_project(session, project_id)
        a = review.record_review_decision(
            session,
            project,
            stage=body.stage,
            decision=body.decision,
            reviewer=body.reviewer,
            notes=body.notes,
        )
        return {
            "id": a.id,
            "stage": a.stage.value,
            "decision": a.decision.value,
            "resulting_state": a.resulting_state,
            "state": project.state,
        }

    @router.post("/projects/{project_id}/export")
    def post_export(project_id: str, session: Session = Depends(get_db)):
        project = services.get_project(session, project_id)
        out = export_project(session, settings, project)
        return {
            "directory": str(out),
            "files": [f for f in ARTIFACT_FILES if (out / f).exists()],
        }

    @router.get("/projects/{project_id}/cost-report")
    def get_cost_report(project_id: str, session: Session = Depends(get_db)):
        project = services.get_project(session, project_id)
        return cost_report(session, project)

    @router.get("/projects/{project_id}/llm-calls")
    def get_llm_calls(project_id: str, session: Session = Depends(get_db)):
        services.get_project(session, project_id)
        rows = session.scalars(
            select(LLMCall)
            .where(LLMCall.project_id == project_id)
            .order_by(LLMCall.created_at)
        ).all()
        return [
            {
                "id": c.id,
                "task": c.task,
                "stage": c.stage,
                "model": c.model,
                "status": c.status.value,
                "cache_hit": c.cache_hit,
                "tokens_in": c.tokens_in,
                "tokens_out": c.tokens_out,
                "web_search_requests": c.web_search_requests,
                "latency_ms": c.latency_ms,
                "cost_eur": c.cost_eur,
                "error_code": c.error_code,
                "created_at": c.created_at,
            }
            for c in rows
        ]

    # ----------------------------------------------------------- budget
    @router.get("/budget")
    def get_budget(project_id: str | None = None, session: Session = Depends(get_db)):
        return budget_getter().snapshot(session, project_id).as_dict()

    @router.put("/budget")
    def put_budget(body: BudgetUpdate, session: Session = Depends(get_db)):
        updates = {
            k: v
            for k, v in body.model_dump(exclude={"actor", "note"}).items()
            if v is not None
        }
        if not updates:
            raise HTTPException(status_code=422, detail="no limit provided")
        try:
            merged = budget_getter().set_limits(
                session, updates, actor=body.actor, note=body.note
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return merged

    return router
