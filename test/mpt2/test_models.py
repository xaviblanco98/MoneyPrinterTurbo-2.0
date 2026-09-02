from __future__ import annotations

from datetime import timezone

import pytest
from sqlalchemy.exc import IntegrityError

from mpt2 import services
from mpt2.models import (
    Asset,
    QualityCheck,
    ResearchClaim,
    ResearchSource,
    Scene,
    Script,
    ScriptSection,
    Storyboard,
)
from mpt2.models.enums import (
    AssetKind,
    AssetLicense,
    AssetType,
    ClaimKind,
    ProjectState,
    SectionRole,
)


def _project(session, channel_config):
    channel = services.create_channel(session, channel_config)
    return services.create_project(session, channel, title="T", topic="topic")


def test_channel_and_project_defaults(session, channel_config):
    project = _project(session, channel_config)
    session.commit()
    assert len(project.id) == 32
    assert project.state == ProjectState.idea
    assert project.language == "en-US"
    assert project.created_at.tzinfo == timezone.utc
    assert project.channel.slug == "test-channel"
    assert project.channel.allowed_sources == ["reuters.com", "sec.gov"]


def test_create_channel_is_idempotent_on_slug(session, channel_config):
    first = services.create_channel(session, channel_config)
    second = services.create_channel(session, channel_config)
    assert first.id == second.id
    assert len(services.list_channels(session)) == 1


def test_upsert_channel_updates_fields(session, channel_config):
    services.create_channel(session, channel_config)
    updated = channel_config.model_copy(update={"tone": "playful"})
    channel = services.upsert_channel(session, updated)
    assert channel.tone == "playful"
    assert len(services.list_channels(session)) == 1


def test_research_script_storyboard_graph(session, channel_config):
    project = _project(session, channel_config)
    source = ResearchSource(
        project_id=project.id, url="https://sec.gov/x", title="S-1", reliability=5
    )
    claim = ResearchClaim(
        project_id=project.id,
        text="Valued at $47bn",
        kind=ClaimKind.fact,
        entities=["WeWork"],
    )
    claim.sources.append(source)
    session.add_all([source, claim])
    session.flush()  # claim.id is assigned on flush
    script = Script(project_id=project.id, language="en-US", version=1)
    script.sections.append(
        ScriptSection(
            position=0, role=SectionRole.hook, text="Hook", claim_ids=[claim.id]
        )
    )
    script.sections.append(
        ScriptSection(position=1, role=SectionRole.development, text="Body")
    )
    storyboard = Storyboard(project_id=project.id, script_id=None, aspect="16:9")
    storyboard.scenes.append(
        Scene(
            position=0,
            narration="Hook",
            asset_type=AssetType.chart,
            search_terms=["wework hq"],
        )
    )
    session.add_all([script, storyboard])
    session.commit()

    session.expire_all()
    loaded = session.get(Script, script.id)
    assert [s.role for s in loaded.sections] == [
        SectionRole.hook,
        SectionRole.development,
    ]
    assert loaded.sections[0].claim_ids == [claim.id]
    assert session.get(ResearchClaim, claim.id).sources[0].url == "https://sec.gov/x"
    assert session.get(Storyboard, storyboard.id).scenes[0].search_terms == [
        "wework hq"
    ]


def test_asset_requires_license_default_unknown(session, channel_config):
    project = _project(session, channel_config)
    asset = Asset(project_id=project.id, kind=AssetKind.video, provider="pexels")
    session.add(asset)
    session.commit()
    assert asset.license == AssetLicense.unknown


def test_unique_constraints(session, channel_config):
    project = _project(session, channel_config)
    session.add(Script(project_id=project.id, language="en", version=1))
    session.add(Script(project_id=project.id, language="en", version=1))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_foreign_keys_enforced(session):
    session.add(QualityCheck(project_id="does-not-exist"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
