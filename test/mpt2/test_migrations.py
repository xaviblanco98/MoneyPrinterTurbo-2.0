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
    "llm_calls",
    "llm_cache",
    "budget_limits",
    "research_plans",
    "search_queries",
    "dossiers",
    "editorial_options",
    "review_events",
}
HEAD_REVISION = "0002"


def test_upgrade_from_scratch_creates_all_tables(tmp_path: Path):
    db_path = tmp_path / "fresh" / "mpt2.sqlite3"
    assert not db_path.exists()
    dbmod.run_migrations(db_path)
    engine = dbmod.make_engine(db_path)
    try:
        tables = set(inspect(engine).get_table_names())
        assert EXPECTED_TABLES <= tables
        assert "alembic_version" in tables
        assert dbmod.current_revision(engine) == dbmod.head_revision() == HEAD_REVISION
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


def test_upgrade_from_0001_with_data(tmp_path: Path):
    """An H1 database with rows upgrades to head and gets sane defaults."""
    import sqlite3

    db_path = tmp_path / "h1.sqlite3"
    dbmod.run_migrations(db_path, "0001")
    con = sqlite3.connect(db_path)
    con.execute(
        "insert into channels (id,slug,name,language,country,niche,audience,tone,target_duration_s,voice,visual_style,allowed_sources,banned_sources,max_budget_eur,quality_threshold,active,created_at,updated_at) values ('c1','s','n','en-US','US','n','a','t',480,'v','vs','[]','[]',0,70,1,'2026-01-01','2026-01-01')"
    )
    con.execute(
        "insert into video_projects (id,channel_id,title,topic,format,language,state,state_updated_at,created_at,updated_at) values ('p1','c1','t','topic','long','en-US','idea','2026-01-01','2026-01-01','2026-01-01')"
    )
    con.execute(
        "insert into research_claims (id,project_id,text,kind,confidence,verified,entities,created_at,updated_at) values ('cl1','p1','x','fact',0.5,0,'[]','2026-01-01','2026-01-01')"
    )
    con.commit()
    con.close()
    dbmod.run_migrations(db_path, "head")
    engine = dbmod.make_engine(db_path)
    try:
        from mpt2.models import ResearchClaim, VideoProject

        with dbmod.make_session_factory(engine)() as session:
            claim = session.get(ResearchClaim, "cl1")
            assert (
                claim.importance.value == "medium"
                and claim.verification_status.value == "unverified"
            )
            assert claim.evidence == [] and claim.run == 1
            assert session.get(VideoProject, "p1").pipeline_run == 1
        with engine.connect() as conn:
            assert (
                compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
            )
    finally:
        engine.dispose()
