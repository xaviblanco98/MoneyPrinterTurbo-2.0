"""FastAPI application for mpt2 (``/api/v2``).

It is a separate ASGI app from the upstream one so MoneyPrinterTurbo keeps
running untouched. Build it with ``create_app(settings)``; tests pass their
own settings and database path.
"""

from __future__ import annotations

import hmac
from typing import Iterator

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy.orm import Session

import mpt2
from mpt2 import db as dbmod
from mpt2 import services
from mpt2.api import schemas
from mpt2.errors import InvalidTransitionError, MPT2Error, NotFoundError
from mpt2.models import PipelineJob
from mpt2.models.enums import ProjectState
from mpt2.settings import Settings, get_settings
from mpt2.state_machine import allowed_transitions
from sqlalchemy import select


def create_app(
    settings: Settings | None = None,
    *,
    run_migrations: bool = True,
    backend=None,
    research_provider=None,
) -> FastAPI:
    settings = settings or get_settings()
    warnings = settings.validate_runtime()
    for warning in warnings:
        logger.warning(f"mpt2 config: {warning}")
    if run_migrations:
        dbmod.run_migrations(settings.db_path)
    engine = dbmod.make_engine(settings.db_path)
    session_factory = dbmod.make_session_factory(engine)

    app = FastAPI(title="MoneyPrinterTurbo 2.0 API", version=mpt2.__version__)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.config_warnings = warnings

    # Editorial pipeline (H2): built lazily so an unconfigured LLM never blocks the API.
    from mpt2.api.editorial import build_router
    from mpt2.costs.guard import BudgetGuard
    from mpt2.jobs import JobQueue
    from mpt2.pipeline.runner import Pipeline, build_context
    from mpt2.pipeline.worker import Worker

    budget = BudgetGuard(settings)
    app.state.budget = budget
    app.state.pipeline = None
    app.state.worker = None

    def get_pipeline() -> Pipeline:
        if app.state.pipeline is None:
            ctx = build_context(
                settings,
                session_factory,
                backend=backend,
                research=research_provider,
                budget=budget,
            )
            queue = JobQueue(
                session_factory,
                max_attempts=settings.job_max_attempts,
                retry_base_seconds=settings.job_retry_base_seconds,
                stale_lock_seconds=settings.job_stale_lock_seconds,
            )
            app.state.pipeline = Pipeline(ctx, queue)
            app.state.worker = Worker(queue, session_factory)
        return app.state.pipeline

    def get_worker() -> Worker:
        get_pipeline()
        return app.state.worker

    # ------------------------------------------------------- dependencies
    def get_db() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
        expected = settings.api_key
        if not expected:
            return
        provided = x_api_key or ""
        if not hmac.compare_digest(provided.encode(), expected.encode()):
            raise HTTPException(status_code=401, detail="invalid or missing x-api-key")

    protected = [Depends(require_api_key)]

    # ---------------------------------------------------- error handling
    @app.exception_handler(MPT2Error)
    async def _mpt2_error(_request: Request, exc: MPT2Error) -> JSONResponse:
        status = (
            404
            if isinstance(exc, NotFoundError)
            else 409
            if isinstance(exc, InvalidTransitionError)
            else 400
        )
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "module": exc.module,
                }
            },
        )

    # ------------------------------------------------------------ routes
    @app.get("/api/v2/health", response_model=schemas.HealthOut)
    def health() -> schemas.HealthOut:
        current = dbmod.current_revision(engine)
        head = dbmod.head_revision()
        return schemas.HealthOut(
            status="ok",
            version=mpt2.__version__,
            database=str(settings.db_path),
            schema_revision=current,
            schema_head=head,
            schema_current=current == head,
            warnings=warnings,
        )

    @app.post(
        "/api/v2/channels",
        response_model=schemas.ChannelOut,
        status_code=201,
        dependencies=protected,
    )
    def create_channel(body: schemas.ChannelCreate, session: Session = Depends(get_db)):
        return services.create_channel(session, body)

    @app.get(
        "/api/v2/channels",
        response_model=list[schemas.ChannelOut],
        dependencies=protected,
    )
    def list_channels(session: Session = Depends(get_db)):
        return services.list_channels(session)

    @app.get(
        "/api/v2/channels/{channel_id}",
        response_model=schemas.ChannelOut,
        dependencies=protected,
    )
    def get_channel(channel_id: str, session: Session = Depends(get_db)):
        return services.get_channel(session, channel_id)

    @app.post(
        "/api/v2/projects",
        response_model=schemas.ProjectOut,
        status_code=201,
        dependencies=protected,
    )
    def create_project(body: schemas.ProjectCreate, session: Session = Depends(get_db)):
        channel = services.get_channel(session, body.channel_id)
        return services.create_project(
            session,
            channel,
            title=body.title,
            topic=body.topic,
            format=body.format,
            language=body.language,
            notes=body.notes,
        )

    @app.get(
        "/api/v2/projects",
        response_model=list[schemas.ProjectOut],
        dependencies=protected,
    )
    def list_projects(
        channel_id: str | None = None, session: Session = Depends(get_db)
    ):
        return services.list_projects(session, channel_id=channel_id)

    @app.get(
        "/api/v2/projects/{project_id}",
        response_model=schemas.ProjectOut,
        dependencies=protected,
    )
    def get_project(project_id: str, session: Session = Depends(get_db)):
        return services.get_project(session, project_id)

    @app.get(
        "/api/v2/projects/{project_id}/state",
        response_model=schemas.ProjectStateOut,
        dependencies=protected,
    )
    def get_project_state(project_id: str, session: Session = Depends(get_db)):
        project = services.get_project(session, project_id)
        return schemas.ProjectStateOut(
            project_id=project.id,
            state=ProjectState(project.state),
            failed_from_state=project.failed_from_state,
            allowed_transitions=sorted(
                allowed_transitions(project), key=lambda s: s.value
            ),
            history=[
                schemas.TransitionOut.model_validate(t) for t in project.transitions
            ],
        )

    @app.post(
        "/api/v2/projects/{project_id}/transition",
        response_model=schemas.ProjectStateOut,
        dependencies=protected,
    )
    def transition_project(
        project_id: str,
        body: schemas.TransitionRequest,
        session: Session = Depends(get_db),
    ):
        project = services.get_project(session, project_id)
        services.advance_project(
            session, project, body.to_state, actor=body.actor, reason=body.reason
        )
        session.flush()
        session.refresh(project)
        return get_project_state(project_id, session)

    @app.post(
        "/api/v2/projects/{project_id}/approvals",
        response_model=schemas.ApprovalOut,
        status_code=201,
        dependencies=protected,
    )
    def approve_project(
        project_id: str,
        body: schemas.ApprovalRequest,
        session: Session = Depends(get_db),
    ):
        project = services.get_project(session, project_id)
        return services.record_approval(
            session,
            project,
            stage=body.stage,
            decision=body.decision,
            reviewer=body.reviewer,
            notes=body.notes,
        )

    @app.get(
        "/api/v2/projects/{project_id}/approvals",
        response_model=list[schemas.ApprovalOut],
        dependencies=protected,
    )
    def list_approvals(project_id: str, session: Session = Depends(get_db)):
        project = services.get_project(session, project_id)
        return sorted(project.approvals, key=lambda a: a.created_at)

    @app.get(
        "/api/v2/projects/{project_id}/errors",
        response_model=list[schemas.ErrorOut],
        dependencies=protected,
    )
    def list_errors(project_id: str, session: Session = Depends(get_db)):
        services.get_project(session, project_id)
        return services.project_errors(session, project_id)

    @app.get(
        "/api/v2/projects/{project_id}/jobs",
        response_model=list[schemas.JobOut],
        dependencies=protected,
    )
    def list_jobs(project_id: str, session: Session = Depends(get_db)):
        services.get_project(session, project_id)
        return session.scalars(
            select(PipelineJob)
            .where(PipelineJob.project_id == project_id)
            .order_by(PipelineJob.created_at)
        ).all()

    @app.get(
        "/api/v2/projects/{project_id}/costs",
        response_model=schemas.CostSummaryOut,
        dependencies=protected,
    )
    def list_costs(project_id: str, session: Session = Depends(get_db)):
        services.get_project(session, project_id)
        entries, total = services.project_costs(session, project_id)
        return schemas.CostSummaryOut(
            project_id=project_id,
            total_est_cost_eur=total,
            entries=[schemas.CostOut.model_validate(e) for e in entries],
        )

    @app.post(
        "/api/v2/projects/{project_id}/costs",
        response_model=schemas.CostOut,
        status_code=201,
        dependencies=protected,
    )
    def add_cost(
        project_id: str, body: schemas.CostCreate, session: Session = Depends(get_db)
    ):
        project = services.get_project(session, project_id)
        return services.record_cost(
            session,
            channel_id=project.channel_id,
            project_id=project.id,
            provider=body.provider,
            stage=body.stage,
            units=body.units,
            unit_type=body.unit_type,
            est_cost_eur=body.est_cost_eur,
            note=body.note,
        )

    app.include_router(
        build_router(get_db, get_pipeline, get_worker, lambda: budget, settings),
        dependencies=protected,
    )
    return app
