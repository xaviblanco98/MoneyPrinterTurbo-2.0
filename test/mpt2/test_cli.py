from __future__ import annotations

import json
from pathlib import Path

import pytest

from mpt2 import db as dbmod
from mpt2.__main__ import main

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MPT2_ENV", "test")
    monkeypatch.setenv("MPT2_DB_PATH", str(tmp_path / "db" / "cli.sqlite3"))
    monkeypatch.setenv("MPT2_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("MPT2_API_KEY", "cli-secret")
    return tmp_path


def test_check_config_masks_secrets_and_dry_run_does_not_create_dirs(env, capsys):
    assert main(["--no-env-file", "check-config", "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["settings"]["api_key"] == "***"
    assert "cli-secret" not in json.dumps(out)
    assert not (env / "storage").exists()


def test_check_config_reports_invalid_config(monkeypatch, capsys):
    monkeypatch.setenv("MPT2_LISTEN_PORT", "0")
    assert main(["--no-env-file", "check-config"]) == 2
    assert "CONFIG INVALID" in capsys.readouterr().err


def test_migrate_then_import_channel(env, capsys):
    assert main(["--no-env-file", "migrate"]) == 0
    head = dbmod.head_revision()
    assert f"schema revision: {head} (head: {head})" in capsys.readouterr().out
    assert (
        main(
            [
                "--no-env-file",
                "import-channel",
                str(REPO_ROOT / "channels" / "business-stories-en.yaml"),
            ]
        )
        == 0
    )
    first = json.loads(capsys.readouterr().out)
    assert first["slug"] == "business-stories-en"
    # Importing again updates in place instead of duplicating.
    assert (
        main(
            [
                "--no-env-file",
                "import-channel",
                str(REPO_ROOT / "channels" / "business-stories-en.yaml"),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["id"] == first["id"]
    engine = dbmod.make_engine(env / "db" / "cli.sqlite3")
    try:
        assert dbmod.schema_is_current(engine)
    finally:
        engine.dispose()


def test_import_channel_requires_migrated_schema(env, capsys):
    assert (
        main(
            [
                "--no-env-file",
                "import-channel",
                str(REPO_ROOT / "channels" / "business-stories-en.yaml"),
            ]
        )
        == 2
    )
    assert "migrate" in capsys.readouterr().err


def test_env_file_option_is_used(env, tmp_path: Path, capsys):
    env_file = tmp_path / "custom.env"
    env_file.write_text("MPT2_OLLAMA_MODEL=llama3.2:3b\n")
    assert main(["--env-file", str(env_file), "check-config", "--dry-run"]) == 0
    assert (
        json.loads(capsys.readouterr().out)["settings"]["ollama_model"] == "llama3.2:3b"
    )
