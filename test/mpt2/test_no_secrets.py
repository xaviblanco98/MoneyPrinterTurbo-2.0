"""Guard against secrets and local artifacts being committed."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Real-looking credential shapes. Placeholders in docs use words, not keys.
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI-style
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),  # Google API key
    re.compile(r"ghp_[A-Za-z0-9]{36}"),  # GitHub token
    re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}"),  # Slack
    re.compile(
        r"(?i)(api[_-]?key|secret|token|password)\s*[=:]\s*['\"][A-Za-z0-9_\-]{24,}['\"]"
    ),
]
FORBIDDEN_TRACKED = {".env", "config.toml"}


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line]


def test_no_env_or_config_toml_tracked():
    tracked = set(_tracked_files())
    assert not (tracked & FORBIDDEN_TRACKED), tracked & FORBIDDEN_TRACKED
    assert ".env.example" in tracked


def test_env_example_has_no_values_for_secrets():
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for line in text.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, _, value = line.partition("=")
            if any(word in key for word in ("KEY", "SECRET", "TOKEN", "PASSWORD")):
                assert value.strip() == "", f"{key} must be empty in .env.example"


def _mpt2_source_files() -> list[Path]:
    files = [REPO_ROOT / ".env.example"]
    for folder in ("mpt2", "test/mpt2", "channels", "docs/mpt2"):
        for path in (REPO_ROOT / folder).rglob("*"):
            if (
                path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix
                in {".py", ".yaml", ".yml", ".md", ".ini", ".mako", ".example"}
            ):
                files.append(path)
    return files


def test_mpt2_files_contain_no_secret_like_strings():
    files = _mpt2_source_files()
    assert len(files) > 10
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in SECRET_PATTERNS:
            assert not pattern.search(text), (
                f"secret-like string in {path.relative_to(REPO_ROOT)}"
            )
