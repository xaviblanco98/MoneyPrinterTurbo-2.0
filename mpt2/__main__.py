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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
