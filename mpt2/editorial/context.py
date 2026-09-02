"""Shared helpers for the editorial stage handlers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from mpt2.contracts import ResearchProvider
from mpt2.llm.client import LLMClient
from mpt2.models import Channel, ResearchClaim, ResearchSource, VideoProject
from mpt2.models.enums import SourceStatus, VerificationStatus
from mpt2.settings import Settings

WORDS_PER_MINUTE = 150.0
WORDS_PER_SECOND = WORDS_PER_MINUTE / 60.0


@dataclass
class PipelineContext:
    settings: Settings
    llm: LLMClient
    research: ResearchProvider
    session_factory: Callable[[], Session]


def channel_dict(channel: Channel) -> dict[str, Any]:
    return {
        "id": channel.id,
        "slug": channel.slug,
        "name": channel.name,
        "language": channel.language,
        "country": channel.country,
        "niche": channel.niche,
        "audience": channel.audience,
        "tone": channel.tone,
        "visual_style": channel.visual_style,
        "target_duration_s": channel.target_duration_s,
        "voice": channel.voice,
        "allowed_sources": list(channel.allowed_sources or []),
        "banned_sources": list(channel.banned_sources or []),
        "quality_threshold": channel.quality_threshold,
    }


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9À-ÿ'’%$€£-]+", text or ""))


def est_seconds(text: str) -> float:
    return round(word_count(text) / WORDS_PER_SECOND, 1)


def accepted_sources(
    session: Session, project: VideoProject, run: int | None = None
) -> list[ResearchSource]:
    stmt = select(ResearchSource).where(
        ResearchSource.project_id == project.id,
        ResearchSource.status == SourceStatus.accepted,
        ResearchSource.run == (run or project.pipeline_run),
    )
    return list(
        session.scalars(
            stmt.order_by(ResearchSource.is_primary.desc(), ResearchSource.created_at)
        ).all()
    )


def source_dict(
    source: ResearchSource, *, with_snippets: bool = True
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": source.id,
        "title": source.title,
        "url": source.canonical_url or source.url,
        "domain": source.domain,
        "primary": source.is_primary,
        "source_type": source.source_type,
        "date": source.published_at.isoformat() if source.published_at else None,
        "page_age": None,
        "reliability": source.reliability,
    }
    if with_snippets:
        data["snippets"] = list(source.snippets or [])[:8]
    return data


def project_claims(
    session: Session,
    project: VideoProject,
    *,
    run: int | None = None,
    usable_only: bool = False,
) -> list[ResearchClaim]:
    stmt = select(ResearchClaim).where(
        ResearchClaim.project_id == project.id,
        ResearchClaim.run == (run or project.pipeline_run),
        ResearchClaim.discarded.is_(False),
    )
    claims = list(session.scalars(stmt.order_by(ResearchClaim.created_at)).all())
    if usable_only:
        claims = [
            c
            for c in claims
            if c.verification_status
            in (VerificationStatus.supported, VerificationStatus.weak)
        ]
    return claims


def claim_dict(claim: ResearchClaim, *, with_evidence: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": claim.id,
        "text": claim.text,
        "kind": str(getattr(claim.kind, "value", claim.kind)),
        "importance": str(getattr(claim.importance, "value", claim.importance)),
        "confidence": claim.confidence,
        "status": str(
            getattr(claim.verification_status, "value", claim.verification_status)
        ),
        "geographic_scope": claim.geographic_scope,
        "time_period": claim.time_period,
        "supporting_source_ids": [s.id for s in claim.sources],
        "contradicting_source_ids": list(claim.contradicting_source_ids or []),
    }
    if with_evidence:
        data["evidence"] = list(claim.evidence or [])
    return data


NUMBER_RE = re.compile(
    r"(?<![A-Za-z])(\d[\d,]*\.?\d*)\s*(%|percent|billion|million|thousand|bn|m\b|k\b)?",
    re.IGNORECASE,
)


def numeric_tokens(text: str) -> list[str]:
    """Digit sequences that look like data (amounts, percentages, years)."""
    tokens: list[str] = []
    for match in NUMBER_RE.finditer(text or ""):
        raw = match.group(1).replace(",", "").rstrip(".")
        if not raw or not any(ch.isdigit() for ch in raw):
            continue
        tokens.append(raw)
    return tokens


def normalize_digits(text: str) -> set[str]:
    return {t for t in numeric_tokens(text)}


def claims_text_blob(claims: list[ResearchClaim]) -> str:
    parts: list[str] = []
    for claim in claims:
        parts.append(claim.text)
        for ev in claim.evidence or []:
            parts.append(str(ev.get("text", "")))
    return "\n".join(parts)


def unsupported_numbers(section_text: str, claims: list[ResearchClaim]) -> list[str]:
    """Numbers in the section that do not appear in the referenced claims or their evidence."""
    have = normalize_digits(claims_text_blob(claims))
    have_compact = {h.replace(".", "") for h in have}
    missing: list[str] = []
    for token in numeric_tokens(section_text):
        compact = token.replace(".", "")
        if token in have or compact in have_compact:
            continue
        # Allow ordinal-ish small numbers (one to twelve written as digits) used in narration.
        if compact.isdigit() and int(compact) <= 12:
            continue
        missing.append(token)
    return sorted(set(missing))
