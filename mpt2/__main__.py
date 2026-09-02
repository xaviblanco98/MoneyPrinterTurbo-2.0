"""Command line entry point: ``python -m mpt2 <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mpt2 import db as dbmod
from mpt2.channels import load_channel_yaml
from mpt2.errors import SettingsError
from mpt2.settings import Settings


def _settings(args: argparse.Namespace) -> Settings:
    env_file = None if args.no_env_file else args.env_file
    return Settings.from_env(env_file=env_file)


def cmd_check_config(args: argparse.Namespace) -> int:
    try:
        settings = _settings(args)
        warnings = settings.validate_runtime(create_dirs=not args.dry_run)
    except SettingsError as exc:
        print(f"CONFIG INVALID: {exc.message}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"settings": settings.safe_dict(), "warnings": warnings},
            indent=2,
            default=str,
        )
    )
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    settings = _settings(args)
    settings.validate_runtime()
    dbmod.run_migrations(settings.db_path, args.revision)
    engine = dbmod.make_engine(settings.db_path)
    print(f"database: {settings.db_path}")
    print(
        f"schema revision: {dbmod.current_revision(engine)} (head: {dbmod.head_revision()})"
    )
    return 0


def cmd_import_channel(args: argparse.Namespace) -> int:
    from mpt2 import services

    settings = _settings(args)
    settings.validate_runtime()
    config = load_channel_yaml(Path(args.file))
    engine = dbmod.make_engine(settings.db_path)
    if not dbmod.schema_is_current(engine):
        print(
            "database schema is not current; run `python -m mpt2 migrate` first",
            file=sys.stderr,
        )
        return 2
    factory = dbmod.make_session_factory(engine)
    with dbmod.session_scope(factory) as session:
        channel = services.upsert_channel(session, config)
        print(
            json.dumps({"id": channel.id, "slug": channel.slug, "name": channel.name})
        )
    return 0


def _runtime(args: argparse.Namespace):
    """Settings + session factory + pipeline for project commands."""
    from mpt2.costs.guard import BudgetGuard
    from mpt2.jobs import JobQueue
    from mpt2.pipeline.runner import Pipeline, build_context
    from mpt2.pipeline.worker import Worker

    settings = _settings(args)
    settings.validate_runtime()
    engine = dbmod.make_engine(settings.db_path)
    if not dbmod.schema_is_current(engine):
        raise SystemExit(
            "database schema is not current; run `python -m mpt2 migrate` first"
        )
    factory = dbmod.make_session_factory(engine)
    budget = BudgetGuard(settings)
    ctx = build_context(settings, factory, budget=budget)
    queue = JobQueue(
        factory,
        max_attempts=settings.job_max_attempts,
        retry_base_seconds=settings.job_retry_base_seconds,
        stale_lock_seconds=settings.job_stale_lock_seconds,
    )
    pipeline = Pipeline(ctx, queue)
    return settings, factory, pipeline, Worker(queue, factory), budget


def cmd_project_create(args: argparse.Namespace) -> int:
    from mpt2 import services

    settings, factory, pipeline, worker, _ = _runtime(args)
    notes = (
        json.dumps({"required_topics": [t for t in (args.required_topic or [])]})
        if args.required_topic
        else None
    )
    with dbmod.session_scope(factory) as session:
        channel = services.get_channel(session, args.channel)
        project = services.create_project(
            session,
            channel,
            title=args.title,
            topic=args.topic or args.title,
            notes=notes,
        )
        print(
            json.dumps(
                {"id": project.id, "title": project.title, "state": project.state.value}
            )
        )
    return 0


def cmd_project_run(args: argparse.Namespace) -> int:
    from mpt2 import services
    from mpt2.models.enums import ProjectState

    settings, factory, pipeline, worker, _ = _runtime(args)
    with dbmod.session_scope(factory) as session:
        project = services.get_project(session, args.project_id)
        if (
            ProjectState(project.state) in (ProjectState.idea, ProjectState.researching)
            and not project.jobs
        ):
            job = pipeline.start(session, project, actor=args.actor, reason=args.reason)
        elif args.from_stage:
            job = pipeline.rerun_from(
                session, project, args.from_stage, actor=args.actor, reason=args.reason
            )
        else:
            job = pipeline.resume(
                session, project, actor=args.actor, reason=args.reason
            )
        print(f"queued stage {job.stage} (job {job.id})")
    ran = worker.run_until_idle(max_jobs=args.max_jobs)
    with factory() as session:
        project = services.get_project(session, args.project_id)
        print(
            json.dumps(
                {
                    "project_id": project.id,
                    "jobs_run": ran,
                    "state": project.state.value,
                    "run": project.pipeline_run,
                }
            )
        )
    return 0


def cmd_project_status(args: argparse.Namespace) -> int:
    from mpt2 import services
    from mpt2.editorial.artifacts import cost_report
    from mpt2.editorial.review import package_blockers

    settings, factory, pipeline, worker, budget = _runtime(args)
    with factory() as session:
        project = services.get_project(session, args.project_id)
        jobs = [
            {
                "stage": j.stage,
                "status": j.status.value,
                "attempts": j.attempts,
                "error": j.error_code,
            }
            for j in project.jobs
        ]
        report = cost_report(session, project)
        print(
            json.dumps(
                {
                    "id": project.id,
                    "title": project.title,
                    "state": project.state.value,
                    "run": project.pipeline_run,
                    "jobs": jobs,
                    "blockers": package_blockers(session, project),
                    "cost_eur": report["total_cost_eur"],
                    "llm_calls": report["llm_calls"],
                    "web_searches": report["web_searches"],
                    "budget": budget.snapshot(session, project.id).as_dict(),
                },
                indent=2,
                default=str,
            )
        )
    return 0


def cmd_project_export(args: argparse.Namespace) -> int:
    from mpt2 import services
    from mpt2.editorial.artifacts import export_project

    settings, factory, *_ = _runtime(args)
    with factory() as session:
        project = services.get_project(session, args.project_id)
        out = export_project(session, settings, project)
        print(
            json.dumps(
                {"directory": str(out), "files": sorted(p.name for p in out.iterdir())}
            )
        )
    return 0


def cmd_project_review(args: argparse.Namespace) -> int:
    from mpt2 import services
    from mpt2.editorial.review import record_review_decision

    settings, factory, *_ = _runtime(args)
    with dbmod.session_scope(factory) as session:
        project = services.get_project(session, args.project_id)
        approval = record_review_decision(
            session,
            project,
            stage=args.stage,
            decision=args.decision,
            reviewer=args.reviewer,
            notes=args.notes,
        )
        print(
            json.dumps(
                {
                    "approval_id": approval.id,
                    "resulting_state": approval.resulting_state,
                    "state": project.state.value,
                }
            )
        )
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    settings, factory, pipeline, worker, _ = _runtime(args)
    if args.once:
        print(json.dumps({"jobs_run": worker.run_until_idle(max_jobs=args.max_jobs)}))
        return 0
    worker.poll_seconds = args.poll
    print("worker running; Ctrl-C to stop")
    worker.run_forever()
    return 0


def cmd_budget(args: argparse.Namespace) -> int:
    settings, factory, pipeline, worker, budget = _runtime(args)
    with dbmod.session_scope(factory) as session:
        if args.budget_command == "set":
            updates = {
                k: getattr(args, k)
                for k in ("warn_eur", "monthly_hard_eur", "project_eur", "per_call_eur")
                if getattr(args, k) is not None
            }
            try:
                merged = budget.set_limits(
                    session, updates, actor=args.actor, note=args.note
                )
            except ValueError as exc:
                print(f"invalid limits: {exc}", file=sys.stderr)
                return 2
            print(json.dumps(merged))
        else:
            print(
                json.dumps(
                    budget.snapshot(session, args.project_id).as_dict(), indent=2
                )
            )
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from mpt2.api.app import create_app

    settings = _settings(args)
    app = create_app(settings)
    print(f"mpt2 API on http://{settings.listen_host}:{settings.listen_port}/docs")
    uvicorn.run(
        app, host=settings.listen_host, port=settings.listen_port, log_level="info"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mpt2", description="MoneyPrinterTurbo 2.0 tools"
    )
    parser.add_argument(
        "--env-file", default=".env", help="path of the .env file (default: .env)"
    )
    parser.add_argument(
        "--no-env-file", action="store_true", help="ignore any .env file"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check-config", help="validate environment configuration")
    p.add_argument(
        "--dry-run", action="store_true", help="do not create storage directories"
    )
    p.set_defaults(func=cmd_check_config)

    p = sub.add_parser("migrate", help="create or upgrade the SQLite schema")
    p.add_argument("--revision", default="head")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser(
        "import-channel", help="create/update a channel from a YAML file"
    )
    p.add_argument("file")
    p.set_defaults(func=cmd_import_channel)

    p = sub.add_parser("serve", help="run the mpt2 HTTP API")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("worker", help="process pending pipeline jobs")
    p.add_argument(
        "--once", action="store_true", help="run until the queue is idle, then exit"
    )
    p.add_argument("--max-jobs", type=int, default=500)
    p.add_argument("--poll", type=float, default=2.0)
    p.set_defaults(func=cmd_worker)

    proj = sub.add_parser("project", help="editorial projects").add_subparsers(
        dest="project_command", required=True
    )
    p = proj.add_parser("create", help="create a project from a topic")
    p.add_argument("--channel", required=True, help="channel id or slug")
    p.add_argument("--title", required=True)
    p.add_argument(
        "--topic", default=None, help="topic description (defaults to the title)"
    )
    p.add_argument(
        "--required-topic",
        action="append",
        help="sub-topic the research must cover (repeatable)",
    )
    p.set_defaults(func=cmd_project_create)
    p = proj.add_parser(
        "run", help="start or resume the pipeline and process jobs until idle"
    )
    p.add_argument("project_id")
    p.add_argument(
        "--from-stage", default=None, help="rerun from this stage (bumps the run)"
    )
    p.add_argument("--actor", default="cli")
    p.add_argument("--reason", default=None)
    p.add_argument("--max-jobs", type=int, default=200)
    p.set_defaults(func=cmd_project_run)
    p = proj.add_parser("status", help="state, jobs, blockers and cost")
    p.add_argument("project_id")
    p.set_defaults(func=cmd_project_status)
    p = proj.add_parser("export", help="write the artifact folder")
    p.add_argument("project_id")
    p.set_defaults(func=cmd_project_export)
    p = proj.add_parser("review", help="record a human decision")
    p.add_argument("project_id")
    p.add_argument(
        "--stage",
        required=True,
        choices=[
            "research",
            "claims",
            "hook",
            "script",
            "storyboard",
            "package",
            "final",
        ],
    )
    p.add_argument(
        "--decision", required=True, choices=["approve", "reject", "changes_requested"]
    )
    p.add_argument("--reviewer", required=True)
    p.add_argument("--notes", default=None)
    p.set_defaults(func=cmd_project_review)

    b = sub.add_parser("budget", help="show or change safety limits").add_subparsers(
        dest="budget_command", required=True
    )
    p = b.add_parser("show")
    p.add_argument("--project-id", default=None)
    p.set_defaults(func=cmd_budget)
    p = b.add_parser("set")
    p.add_argument("--actor", required=True)
    p.add_argument("--note", default=None)
    for key in ("warn_eur", "monthly_hard_eur", "project_eur", "per_call_eur"):
        p.add_argument(f"--{key.replace('_', '-')}", dest=key, type=float, default=None)
    p.set_defaults(func=cmd_budget)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
