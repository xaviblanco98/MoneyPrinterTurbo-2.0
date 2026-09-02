from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mpt2.channels import ChannelConfig, load_channel_yaml
from test.mpt2.conftest import CHANNEL_PAYLOAD

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_valid_config_normalizes_fields():
    cfg = ChannelConfig(
        **{
            **CHANNEL_PAYLOAD,
            "country": "us",
            "allowed_sources": [" SEC.gov ", "sec.gov"],
        }
    )
    assert cfg.country == "US"
    assert cfg.allowed_sources == ["sec.gov"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("slug", "Bad Slug"),
        ("language", "english"),
        ("country", "USA"),
        ("target_duration_s", 5),
        ("target_duration_s", 4000),
        ("quality_threshold", 101),
        ("max_budget_eur", -1),
        ("voice", ""),
    ],
)
def test_invalid_values_rejected(field, value):
    with pytest.raises(ValidationError):
        ChannelConfig(**{**CHANNEL_PAYLOAD, field: value})


def test_missing_required_field_rejected():
    payload = dict(CHANNEL_PAYLOAD)
    del payload["tone"]
    with pytest.raises(ValidationError):
        ChannelConfig(**payload)


def test_allowed_and_banned_cannot_overlap():
    with pytest.raises(ValidationError, match="both allowed and banned"):
        ChannelConfig(**{**CHANNEL_PAYLOAD, "banned_sources": ["sec.gov"]})


def test_pilot_channel_yaml_is_valid():
    cfg = load_channel_yaml(REPO_ROOT / "channels" / "business-stories-en.yaml")
    assert cfg.slug == "business-stories-en"
    assert cfg.language == "en-US"
    assert cfg.max_budget_eur == 0


def test_yaml_must_be_mapping(tmp_path: Path):
    bad = tmp_path / "c.yaml"
    bad.write_text("- just\n- a list\n")
    with pytest.raises(ValueError, match="mapping"):
        load_channel_yaml(bad)
