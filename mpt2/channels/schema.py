"""Editorial configuration of a channel, validated with Pydantic.

The same schema is used by the API (JSON body) and by YAML files under
``channels/``. It never contains secrets; provider keys live in the
environment (see ``mpt2.settings``).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_LANG_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z]{2,4})?$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")


class ChannelConfig(BaseModel):
    slug: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    language: str = Field(description="BCP-47 tag such as en-US or es-ES")
    country: str = Field(description="ISO 3166-1 alpha-2, e.g. US")
    niche: str = Field(min_length=1, max_length=200)
    audience: str = Field(min_length=1)
    tone: str = Field(min_length=1)
    target_duration_s: int = Field(ge=15, le=3600)
    voice: str = Field(min_length=1, max_length=200)
    visual_style: str = Field(min_length=1)
    allowed_sources: list[str] = Field(default_factory=list)
    banned_sources: list[str] = Field(default_factory=list)
    max_budget_eur: float = Field(ge=0.0, default=0.0)
    quality_threshold: float = Field(ge=0.0, le=100.0, default=70.0)

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not _SLUG_RE.match(value):
            raise ValueError("slug must be lowercase letters, digits and hyphens")
        return value

    @field_validator("language")
    @classmethod
    def _language(cls, value: str) -> str:
        if not _LANG_RE.match(value):
            raise ValueError("language must look like 'en', 'en-US' or 'es-ES'")
        return value

    @field_validator("country")
    @classmethod
    def _country(cls, value: str) -> str:
        value = value.upper()
        if not _COUNTRY_RE.match(value):
            raise ValueError("country must be an ISO 3166-1 alpha-2 code")
        return value

    @field_validator("allowed_sources", "banned_sources")
    @classmethod
    def _sources(cls, value: list[str]) -> list[str]:
        cleaned = sorted(
            {item.strip().lower() for item in value if item and item.strip()}
        )
        return cleaned

    @model_validator(mode="after")
    def _no_overlap(self) -> "ChannelConfig":
        overlap = set(self.allowed_sources) & set(self.banned_sources)
        if overlap:
            raise ValueError(
                f"sources cannot be both allowed and banned: {sorted(overlap)}"
            )
        return self


def load_channel_yaml(path: Path | str) -> ChannelConfig:
    """Load and validate a channel YAML file (safe loader only)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at top level")
    return ChannelConfig(**data)
