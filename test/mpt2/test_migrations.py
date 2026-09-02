from __future__ import annotations

from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from mpt2 import db as dbmod
from mpt2.models import Base

EXPECTED_TABLES = {
    "channels",
    "video_projects",
    "project_state_transitions",
    "research_sources",
    "research_claims",
    "research_claim_sources",
    "scripts",
    "script_sections",
    "storyboards",
    "scenes",
    "assets",
    "pipeline_jobs",
    "quality_checks",
    "human_approvals",
    "cost_entries",
}


def test_upgrade_from_scratch_creates_all_tables(tmp_path: Path):
    db_path = tmp_path / "fresh" / "mpt2.sqlite3"
    assert not db_path.exists()
    dbmod.run_migrations(db_path)
    engine = dbmod.make_engine(db_path)
    try:
        tables = set(inspect(engine).get_table_names())
        assert EXPECTED_TABLES <= tables
        assert "alembic_version" in tables
        assert dbmod.current_revision(engine) == dbmod.head_revision() == "0001"
        assert dbmod.schema_is_current(engine)
    finally:
        engine.dispose()


def test_migrations_match_models(migrated_db: Path):
    engine = dbmod.make_engine(migrated_db)
    try:
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
        assert diff == [], diff
    finally:
        engine.dispose()


def test_upgrade_is_idempotent(migrated_db: Path):
    dbmod.run_migrations(migrated_db)
    engine = dbmod.make_engine(migrated_db)
    try:
        assert dbmod.schema_is_current(engine)
    finally:
        engine.dispose()


def test_downgrade_to_base_removes_tables(migrated_db: Path):
    dbmod.downgrade(migrated_db, "base")
    engine = dbmod.make_engine(migrated_db)
    try:
        tables = set(inspect(engine).get_table_names())
        assert not (EXPECTED_TABLES & tables)
        assert dbmod.current_revision(engine) is None
        dbmod.run_migrations(migrated_db)
        assert dbmod.schema_is_current(engine)
    finally:
        engine.dispose()


def test_sqlite_pragmas(engine):
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
