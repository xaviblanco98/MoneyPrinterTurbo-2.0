"""Shared fixtures for mpt2 tests: isolated SQLite database per test."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mpt2 import db as dbmod
from mpt2.api.app import create_app
from mpt2.channels.schema import ChannelConfig
from mpt2.settings import Settings, reset_settings_cache

CHANNEL_PAYLOAD = {
    "slug": "test-channel",
    "name": "Test Channel",
    "language": "en-US",
    "country": "US",
    "niche": "business stories",
    "audience": "adults interested in business",
    "tone": "documentary, calm",
    "target_duration_s": 480,
    "voice": "en-US-AndrewNeural",
    "visual_style": "clean documentary",
    "allowed_sources": ["sec.gov", "reuters.com"],
    "banned_sources": ["tabloids"],
    "max_budget_eur": 0.0,
    "quality_threshold": 75.0,
}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    reset_settings_cache()
    return Settings.from_env(
        {
            "MPT2_ENV": "test",
            "MPT2_DB_PATH": str(tmp_path / "db" / "test.sqlite3"),
            "MPT2_STORAGE_DIR": str(tmp_path / "storage"),
            "MPT2_JOB_RETRY_BASE_SECONDS": "0",
        },
        env_file=None,
    )


@pytest.fixture
def migrated_db(settings: Settings) -> Path:
    dbmod.run_migrations(settings.db_path)
    return settings.db_path


@pytest.fixture
def engine(migrated_db: Path):
    engine = dbmod.make_engine(migrated_db)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(engine):
    return dbmod.make_session_factory(engine)


@pytest.fixture
def session(session_factory):
    session = session_factory()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def channel_config() -> ChannelConfig:
    return ChannelConfig(**CHANNEL_PAYLOAD)


@pytest.fixture
def app(settings: Settings):
    application = create_app(settings)
    yield application
    application.state.engine.dispose()


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)
