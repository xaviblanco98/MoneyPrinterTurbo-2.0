"""Runtime settings loaded exclusively from environment variables.

Secrets never live in files tracked by git. A local ``.env`` file (ignored by
git) may hold them; real environment variables always take precedence over the
file. ``Settings.from_env()`` validates everything at startup and raises
``SettingsError`` listing every problem at once.

Compatibility with upstream: when a stock provider key is not present in the
environment, ``stock_api_keys()`` falls back to the key list stored in the
upstream ``config.toml`` so a machine that already runs MoneyPrinterTurbo keeps
working without duplicating configuration.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from pydantic import BaseModel, Field, ValidationError, field_validator

from mpt2.errors import SettingsError

ENV_PREFIX = "MPT2_"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = REPO_ROOT / ".env"

# Fields whose values must never be printed or returned by the API.
SECRET_FIELDS = frozenset(
    {"api_key", "pexels_api_key", "pixabay_api_key", "llm_api_key"}
)

_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
_URL_RE = re.compile(r"^https?://[^\s/]+(?::\d+)?(?:/.*)?$")


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a minimal ``KEY=value`` file. Lines starting with ``#`` are ignored.

    Supports optional ``export `` prefix and single/double quoted values.
    Deliberately tiny so we do not add a dependency for it.
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


class Settings(BaseModel):
    """Validated configuration for mpt2.

    Environment variable names are the field names upper-cased with the
    ``MPT2_`` prefix, e.g. ``MPT2_DB_PATH``. Provider keys use their
    conventional names without prefix (``PEXELS_API_KEY``).
    """

    env: str = Field(default="development", pattern=r"^(development|test|production)$")
    db_path: Path = Field(default=REPO_ROOT / "storage" / "mpt2" / "mpt2.sqlite3")
    storage_dir: Path = Field(default=REPO_ROOT / "storage" / "mpt2")
    listen_host: str = "127.0.0.1"
    listen_port: int = Field(default=8090, ge=1, le=65535)
    log_level: str = "INFO"
    api_key: str | None = None

    llm_provider: str = Field(default="ollama", min_length=1)
    llm_model: str = ""
    llm_base_url: str = ""
    llm_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "qwen2.5:7b"

    pexels_api_key: str | None = None
    pixabay_api_key: str | None = None

    job_max_attempts: int = Field(default=3, ge=1, le=20)
    job_retry_base_seconds: float = Field(default=5.0, ge=0.0, le=3600.0)
    job_stale_lock_seconds: int = Field(default=900, ge=10)

    # Hard product rule for the MVP: publishing always requires a human.
    require_human_approval: bool = True
    auto_publish: bool = False

    @field_validator("log_level")
    @classmethod
    def _log_level(cls, value: str) -> str:
        upper = value.strip().upper()
        if upper not in _LOG_LEVELS:
            raise ValueError(f"log_level must be one of {sorted(_LOG_LEVELS)}")
        return upper

    @field_validator("ollama_base_url", "llm_base_url")
    @classmethod
    def _url(cls, value: str) -> str:
        value = value.strip()
        if value and not _URL_RE.match(value):
            raise ValueError(f"invalid URL: {value!r}")
        return value.rstrip("/")

    @field_validator("db_path", "storage_dir", mode="before")
    @classmethod
    def _expand_path(cls, value: Any) -> Any:
        if isinstance(value, str):
            return Path(os.path.expanduser(value))
        return value

    @field_validator("auto_publish")
    @classmethod
    def _auto_publish(cls, value: bool) -> bool:
        if value:
            raise ValueError(
                "auto_publish is not allowed in the MVP; human approval is mandatory"
            )
        return value

    # ------------------------------------------------------------------ load
    @classmethod
    def env_var_name(cls, field: str) -> str:
        if field in {"pexels_api_key", "pixabay_api_key"}:
            return field.upper()
        return f"{ENV_PREFIX}{field.upper()}"

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        env_file: Path | None | str = DEFAULT_ENV_FILE,
    ) -> "Settings":
        """Build settings from ``environ`` (defaults to ``os.environ``).

        ``env_file`` values only fill variables missing from ``environ``.
        Pass ``env_file=None`` to ignore any file.
        """
        source: dict[str, str] = {}
        if env_file:
            source.update(parse_env_file(Path(env_file)))
        source.update(dict(os.environ if environ is None else environ))

        raw: dict[str, Any] = {}
        for field in cls.model_fields:
            name = cls.env_var_name(field)
            if name in source and source[name] != "":
                raw[field] = source[name]
        try:
            return cls(**raw)
        except ValidationError as exc:
            problems = "; ".join(
                f"{cls.env_var_name('.'.join(str(p) for p in e['loc']))}: {e['msg']}"
                for e in exc.errors()
            )
            raise SettingsError(f"invalid configuration: {problems}") from exc

    # ------------------------------------------------------------- checks
    def validate_runtime(self, create_dirs: bool = True) -> list[str]:
        """Check the environment can actually be used. Returns warnings.

        Raises ``SettingsError`` for hard problems (unwritable storage).
        """
        problems: list[str] = []
        warnings: list[str] = []
        for directory in (self.storage_dir, self.db_path.parent):
            try:
                if create_dirs:
                    directory.mkdir(parents=True, exist_ok=True)
                if not os.access(_nearest_existing(directory), os.W_OK):
                    problems.append(f"directory not writable: {directory}")
            except OSError as exc:
                problems.append(f"cannot create directory {directory}: {exc}")
        if self.llm_provider == "ollama" and not self.ollama_base_url:
            problems.append(
                "MPT2_OLLAMA_BASE_URL is required when MPT2_LLM_PROVIDER=ollama"
            )
        if self.llm_provider != "ollama" and not self.llm_api_key:
            warnings.append(
                f"MPT2_LLM_API_KEY not set for provider {self.llm_provider!r}; "
                "LLM calls will fail until it is provided"
            )
        if not self.pexels_api_key and not self.pixabay_api_key:
            warnings.append(
                "no stock provider key in the environment (PEXELS_API_KEY / PIXABAY_API_KEY); "
                "falling back to upstream config.toml if present"
            )
        if self.env == "production" and not self.api_key:
            warnings.append("MPT2_API_KEY is empty: the API is unauthenticated")
        if problems:
            raise SettingsError("; ".join(problems))
        return warnings

    # ------------------------------------------------------------ helpers
    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    def safe_dict(self) -> dict[str, Any]:
        """Settings with secrets masked, safe for logs and the API."""
        data = self.model_dump(mode="json")
        for field in SECRET_FIELDS:
            if data.get(field):
                data[field] = "***"
        return data

    def stock_api_keys(self, provider: str) -> list[str]:
        """Keys for a stock provider: environment first, upstream config second."""
        own = {"pexels": self.pexels_api_key, "pixabay": self.pixabay_api_key}.get(
            provider
        )
        if own:
            return [own]
        return list(_upstream_config_list(f"{provider}_api_keys"))


def _nearest_existing(path: Path) -> Path:
    """The path itself or its closest existing ancestor (for dry-run checks)."""
    current = path
    while not current.exists() and current.parent != current:
        current = current.parent
    return current


def _upstream_config_list(key: str) -> Iterable[str]:
    """Read a list value from the upstream config without failing if absent."""
    try:
        from app.config import config as upstream_config  # noqa: WPS433 (lazy import)
    except Exception:  # pragma: no cover - upstream missing or broken
        return []
    value = upstream_config.app.get(key, [])
    if isinstance(value, str):
        value = [value]
    return [v for v in value if isinstance(v, str) and v.strip()]


_cached: Settings | None = None


def get_settings() -> Settings:
    """Process-wide settings, loaded once from the environment."""
    global _cached
    if _cached is None:
        _cached = Settings.from_env()
    return _cached


def reset_settings_cache() -> None:
    global _cached
    _cached = None
