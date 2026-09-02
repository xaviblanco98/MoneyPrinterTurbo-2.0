"""Wires stages to the durable job queue and the project state machine."""

from __future__ import annotations

from typing import Any, Callable

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from mpt2.contracts import ResearchProvider
from mpt2.costs.guard import BudgetGuard
from mpt2.editorial.artifacts import export_project
from mpt2.editorial.context import PipelineContext
from mpt2.errors import StageError
from mpt2.jobs import JobQueue
from mpt2.llm.backend import AnthropicBackend, Backend, FakeBackend
from mpt2.llm.client import LLMClient
from mpt2.models import PipelineJob, VideoProject
from mpt2.models.enums import JobStatus, ProjectState as S
from mpt2.pipeline.stages import FINAL_STATE, STAGE_BY_NAME, STAGES, Stage, next_stage
from mpt2.research.providers import AnthropicWebSearchProvider, FakeResearchProvider
from mpt2.settings import Settings
from mpt2.state_machine import allowed_transitions, transition


def job_key(project: VideoProject, stage: str) -> str:
    return f"{project.id}:{stage}:run{project.pipeline_run}"


def build_backend(settings: Settings, backend: Backend | None = None) -> Backend:
    if backend is not None:
        return backend
    if settings.llm_backend == "fake":
        from mpt2.llm.fake_editorial import EditorialScripter

        return FakeBackend(EditorialScripter())
    if settings.llm_backend == "anthropic":
        if not settings.anthropic_api_key:
            raise StageError(
                "ANTHROPIC_API_KEY is not set",
                code="llm_config",
                module=__name__,
                retryable=False,
            )
        return AnthropicBackend(
            settings.anthropic_api_key,
            timeout_seconds=settings.llm_timeout_seconds,
            web_search_tool_version=settings.web_search_tool_version,
        )
    raise StageError(
        f"unsupported MPT2_LLM_BACKEND={settings.llm_backend!r} for the editorial pipeline (use anthropic or fake)",
        code="llm_config",
        module=__name__,
        retryable=False,
    )


def build_context(
    settings: Settings,
    session_factory: Callable[[], Session],
    *,
    backend: Backend | None = None,
    research: ResearchProvider | None = None,
    budget: BudgetGuard | None = None,
    sleep=None,
) -> PipelineContext:
    backend = build_backend(settings, backend)
    llm = LLMClient(
        settings,
        backend,
        session_factory,
        budget or BudgetGuard(settings),
        **({"sleep": sleep} if sleep else {}),
    )
    if research is None:
        research = (
            FakeResearchProvider()
            if backend.name == "fake"
            else AnthropicWebSearchProvider(
                llm,
                max_uses_per_call=settings.web_search_max_uses_per_call,
                min_snippet_chars=settings.research_min_snippet_chars,
                country=settings.research_user_country,
            )
        )
    return PipelineContext(
        settings=settings, llm=llm, research=research, session_factory=session_factory
    )


class Pipeline:
    """Registers stage handlers on a queue and drives project state."""

    def __init__(self, ctx: PipelineContext, queue: JobQueue):
        self.ctx = ctx
        self.queue = queue
        for stage in STAGES:
            queue.register(stage.name, self._wrap(stage))

    # ---------------------------------------------------------- handlers
    def _wrap(self, stage: Stage):
        def handler(job: PipelineJob, session: Session) -> dict[str, Any]:
            project = session.get(VideoProject, job.project_id)
            if project is None:
                raise StageError(
                    "project not found",
                    code="project_missing",
                    module=__name__,
                    retryable=False,
                )
            if S(project.state) != stage.state:
                raise StageError(
                    f"project is in state {project.state!r}, stage {stage.name} requires {stage.state.value!r}",
                    code="state_mismatch",
                    module=__name__,
                    retryable=False,
                )
            if not job.idempotency_key.endswith(f"run{project.pipeline_run}"):
                raise StageError(
                    "job belongs to a previous pipeline run",
                    code="stale_run",
                    module=__name__,
                    retryable=False,
                )
            try:
                result = stage.handler(self.ctx, job, session) or {}
            except StageError as exc:
                self._maybe_fail_project(
                    session, job, project, exc, retryable=exc.retryable
                )
                raise
            except Exception as exc:  # noqa: BLE001
                self._maybe_fail_project(session, job, project, exc, retryable=True)
                raise
            self._advance(session, project, stage)
            return result

        handler.__module__ = stage.handler.__module__
        handler.__name__ = stage.handler.__name__
        return handler

    def _maybe_fail_project(
        self,
        session: Session,
        job: PipelineJob,
        project: VideoProject,
        exc: Exception,
        *,
        retryable: bool,
    ) -> None:
        last_attempt = job.attempts >= job.max_attempts
        session.rollback()
        self.ctx.llm.replay_orphans(session)
        session.commit()
        if not retryable or last_attempt:
            project = session.get(VideoProject, project.id)
            if S(project.state) is not S.failed and S.failed in allowed_transitions(
                project
            ):
                reason = f"{job.stage}: {getattr(exc, 'code', type(exc).__name__)}: {str(getattr(exc, 'message', exc))[:500]}"
                transition(session, project, S.failed, actor="pipeline", reason=reason)
                session.commit()

    def _advance(self, session: Session, project: VideoProject, stage: Stage) -> None:
        following = next_stage(stage.name)
        if following is None:
            transition(
                session,
                project,
                FINAL_STATE,
                actor="pipeline",
                reason="editorial package complete",
            )
            session.flush()
            export_project(session, self.ctx.settings, project)
            logger.success(f"project {project.id}: editorial package ready for review")
            return
        if following.state != stage.state:
            transition(
                session,
                project,
                following.state,
                actor="pipeline",
                reason=f"{stage.name} done",
            )
        self.queue.enqueue(
            session,
            project.id,
            following.name,
            {"run": project.pipeline_run},
            idempotency_key=job_key(project, following.name),
        )

    # ---------------------------------------------------------- control
    def start(
        self,
        session: Session,
        project: VideoProject,
        *,
        actor: str = "api",
        reason: str | None = None,
    ) -> PipelineJob:
        if S(project.state) is S.idea:
            transition(
                session,
                project,
                S.researching,
                actor=actor,
                reason=reason or "start editorial pipeline",
            )
        elif S(project.state) is not S.researching:
            raise StageError(
                f"cannot start pipeline from state {project.state!r}; use resume or rerun",
                code="invalid_start",
                module=__name__,
                retryable=False,
            )
        stage = STAGES[0]
        return self.queue.enqueue(
            session,
            project.id,
            stage.name,
            {"run": project.pipeline_run},
            idempotency_key=job_key(project, stage.name),
        )

    def resume(
        self,
        session: Session,
        project: VideoProject,
        *,
        actor: str = "api",
        reason: str | None = None,
    ) -> PipelineJob:
        """Resume a failed project at the stage that failed (same run, no new artifacts lost)."""
        if S(project.state) is S.failed:
            origin = project.failed_from_state
            transition(
                session,
                project,
                origin,
                actor=actor,
                reason=reason or "resume after failure",
            )
        state = S(project.state)
        job = session.scalar(
            select(PipelineJob)
            .where(
                PipelineJob.project_id == project.id,
                PipelineJob.status.in_(
                    [
                        JobStatus.failed,
                        JobStatus.needs_human,
                        JobStatus.queued,
                        JobStatus.running,
                    ]
                ),
                PipelineJob.idempotency_key.like(f"%:run{project.pipeline_run}"),
            )
            .order_by(PipelineJob.created_at.desc())
        )
        if job is not None and job.status in (JobStatus.failed, JobStatus.needs_human):
            job.status = JobStatus.queued
            job.attempts = 0
            job.next_run_at = job.updated_at = __import__(
                "mpt2.models", fromlist=["utcnow"]
            ).utcnow()
            job.locked_by = None
            session.flush()
            return job
        if job is not None:
            return job
        stage = next((s for s in STAGES if s.state == state), None)
        if stage is None:
            raise StageError(
                f"nothing to resume in state {state.value}",
                code="invalid_resume",
                module=__name__,
                retryable=False,
            )
        return self.queue.enqueue(
            session,
            project.id,
            stage.name,
            {"run": project.pipeline_run},
            idempotency_key=job_key(project, stage.name),
        )

    def rerun_from(
        self,
        session: Session,
        project: VideoProject,
        stage_name: str,
        *,
        actor: str,
        reason: str | None = None,
    ) -> PipelineJob:
        """Human-requested rerun from a stage: bumps the run and regenerates downstream artifacts.

        Prior-run artifacts stay in the database (versioned by run); human selections of
        options survive because options are only regenerated from the angles stage.
        """
        stage = STAGE_BY_NAME.get(stage_name)
        if stage is None:
            raise StageError(
                f"unknown stage {stage_name!r}",
                code="unknown_stage",
                module=__name__,
                retryable=False,
            )
        if S(project.state) is not stage.state:
            transition(
                session,
                project,
                stage.state,
                actor=actor,
                reason=reason or f"rerun from {stage_name}",
            )
        old_run = project.pipeline_run
        project.pipeline_run = old_run + 1
        self._carry_forward(session, project, stage, old_run)
        session.flush()
        return self.queue.enqueue(
            session,
            project.id,
            stage.name,
            {"run": project.pipeline_run},
            idempotency_key=job_key(project, stage.name),
        )

    def _carry_forward(
        self, session: Session, project: VideoProject, stage: Stage, old_run: int
    ) -> None:
        """Copy artifacts that precede the rerun stage into the new run so handlers find them."""
        from mpt2.editorial.angles import options_for
        from mpt2.models import (
            Dossier,
            ResearchClaim,
            ResearchPlan,
            ResearchSource,
            SearchQuery,
        )

        idx = [s.name for s in STAGES].index(stage.name)
        keep = {s.name for s in STAGES[:idx]}
        new_run = project.pipeline_run
        if "research_plan" in keep:
            for row in session.scalars(
                select(ResearchPlan).where(
                    ResearchPlan.project_id == project.id, ResearchPlan.run == old_run
                )
            ):
                session.add(
                    ResearchPlan(
                        project_id=project.id, run=new_run, content=row.content
                    )
                )
        if "research_search" in keep:
            for row in session.scalars(
                select(SearchQuery).where(
                    SearchQuery.project_id == project.id, SearchQuery.run == old_run
                )
            ):
                row.run = new_run  # move (log is append-only across runs anyway)
            for row in session.scalars(
                select(ResearchSource).where(
                    ResearchSource.project_id == project.id,
                    ResearchSource.run == old_run,
                )
            ):
                row.run = new_run
        if "research_dossier" in keep:
            for row in session.scalars(
                select(Dossier).where(
                    Dossier.project_id == project.id, Dossier.run == old_run
                )
            ):
                session.add(
                    Dossier(
                        project_id=project.id,
                        run=new_run,
                        content=row.content,
                        researched_at=row.researched_at,
                    )
                )
        if "claims_extract" in keep:
            for row in session.scalars(
                select(ResearchClaim).where(
                    ResearchClaim.project_id == project.id, ResearchClaim.run == old_run
                )
            ):
                row.run = new_run
                if "claims_verify" not in keep:
                    row.verification_status = __import__(
                        "mpt2.models.enums", fromlist=["VerificationStatus"]
                    ).VerificationStatus.unverified
                row.used_in_script = False
        if "angles_hooks" in keep:
            # Move (not copy) so scripts and human selections keep pointing at the same ids.
            for row in options_for(session, project, run=old_run):
                row.run = new_run
        if "script_write" in keep:
            from mpt2.models import QualityCheck, Script, Storyboard

            for row in session.scalars(
                select(Script).where(
                    Script.project_id == project.id, Script.run == old_run
                )
            ):
                row.run = new_run
            if "script_fact_check" in keep:
                for row in session.scalars(
                    select(QualityCheck).where(
                        QualityCheck.project_id == project.id,
                        QualityCheck.run == old_run,
                        QualityCheck.kind == "fact_check",
                    )
                ):
                    row.run = new_run
            if "storyboard" in keep:
                for row in session.scalars(
                    select(Storyboard).where(
                        Storyboard.project_id == project.id, Storyboard.run == old_run
                    )
                ):
                    row.run = new_run
