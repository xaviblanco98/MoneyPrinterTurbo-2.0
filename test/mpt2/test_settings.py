from __future__ import annotations

from pathlib import Path

import pytest

from mpt2.errors import SettingsError
from mpt2.settings import Settings, get_settings, parse_env_file, reset_settings_cache


def test_defaults_are_safe():
    settings = Settings.from_env({}, env_file=None)
    assert settings.llm_provider == "ollama"
    assert settings.auto_publish is False
    assert settings.require_human_approval is True
    assert settings.api_key is None
    assert settings.database_url.startswith("sqlite:///")


def test_env_overrides_and_types(tmp_path: Path):
    settings = Settings.from_env(
        {
            "MPT2_DB_PATH": str(tmp_path / "x.sqlite3"),
            "MPT2_LISTEN_PORT": "9001",
            "MPT2_LOG_LEVEL": "debug",
            "MPT2_API_KEY": "s3cret",
            "PEXELS_API_KEY": "pex",
        },
        env_file=None,
    )
    assert settings.listen_port == 9001
    assert settings.log_level == "DEBUG"
    assert settings.db_path == tmp_path / "x.sqlite3"
    assert settings.stock_api_keys("pexels") == ["pex"]


def test_invalid_values_are_reported_together():
    with pytest.raises(SettingsError) as exc:
        Settings.from_env(
            {
                "MPT2_LISTEN_PORT": "99999",
                "MPT2_LOG_LEVEL": "loud",
                "MPT2_ENV": "staging",
            },
            env_file=None,
        )
    message = exc.value.message
    assert "MPT2_LISTEN_PORT" in message
    assert "MPT2_LOG_LEVEL" in message
    assert "MPT2_ENV" in message


def test_auto_publish_cannot_be_enabled():
    with pytest.raises(SettingsError, match="auto_publish"):
        Settings.from_env({"MPT2_AUTO_PUBLISH": "true"}, env_file=None)


def test_invalid_url_rejected():
    with pytest.raises(SettingsError, match="OLLAMA_BASE_URL"):
        Settings.from_env({"MPT2_OLLAMA_BASE_URL": "localhost:11434"}, env_file=None)


def test_env_file_is_read_but_environment_wins(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nexport MPT2_LISTEN_PORT=7000\nMPT2_LOG_LEVEL='WARNING'\nMPT2_OLLAMA_MODEL=\"llama3\"\n"
    )
    parsed = parse_env_file(env_file)
    assert parsed == {
        "MPT2_LISTEN_PORT": "7000",
        "MPT2_LOG_LEVEL": "WARNING",
        "MPT2_OLLAMA_MODEL": "llama3",
    }
    settings = Settings.from_env({"MPT2_LISTEN_PORT": "8001"}, env_file=env_file)
    assert settings.listen_port == 8001
    assert settings.log_level == "WARNING"
    assert settings.ollama_model == "llama3"


def test_missing_env_file_is_ignored(tmp_path: Path):
    settings = Settings.from_env({}, env_file=tmp_path / "missing.env")
    assert settings.listen_port == 8090


def test_safe_dict_masks_secrets():
    settings = Settings.from_env(
        {"MPT2_API_KEY": "topsecret", "PIXABAY_API_KEY": "pix"}, env_file=None
    )
    data = settings.safe_dict()
    assert data["api_key"] == "***"
    assert data["pixabay_api_key"] == "***"
    assert "topsecret" not in str(data)


def test_validate_runtime_creates_dirs_and_warns(tmp_path: Path):
    settings = Settings.from_env(
        {
            "MPT2_DB_PATH": str(tmp_path / "a" / "b.sqlite3"),
            "MPT2_STORAGE_DIR": str(tmp_path / "s"),
        },
        env_file=None,
    )
    warnings = settings.validate_runtime()
    assert (tmp_path / "a").is_dir() and (tmp_path / "s").is_dir()
    assert any("stock provider" in w for w in warnings)


def test_validate_runtime_unwritable_dir(tmp_path: Path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    settings = Settings.from_env(
        {"MPT2_STORAGE_DIR": str(blocker / "sub")}, env_file=None
    )
    with pytest.raises(SettingsError):
        settings.validate_runtime()


def test_get_settings_is_cached(monkeypatch):
    reset_settings_cache()
    monkeypatch.setenv("MPT2_LISTEN_PORT", "8123")
    first = get_settings()
    monkeypatch.setenv("MPT2_LISTEN_PORT", "8124")
    assert get_settings() is first
    reset_settings_cache()
    assert get_settings().listen_port == 8124
    reset_settings_cache()


def test_stock_keys_fall_back_to_upstream_config(monkeypatch):
    from app.config import config as upstream

    monkeypatch.setitem(upstream.app, "pixabay_api_keys", ["from-config"])
    settings = Settings.from_env({}, env_file=None)
    assert settings.stock_api_keys("pixabay") == ["from-config"]
    assert settings.stock_api_keys("unknown") == []
