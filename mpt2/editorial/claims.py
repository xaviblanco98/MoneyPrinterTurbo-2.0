"""Claims extraction and verification (fact-check phase 1)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mpt2.editorial import prompts
from mpt2.editorial.context import (
    PipelineContext,
    accepted_sources,
    claim_dict,
    numeric_tokens,
    project_claims,
    source_dict,
)
from mpt2.editorial.schemas import ClaimsOut, FactCheckOut
from mpt2.errors import StageError
from mpt2.models import Dossier, PipelineJob, ResearchClaim, VideoProject
from mpt2.models.enums import ClaimImportance, ClaimKind, VerificationStatus

CHUNK = 25


def claims_extract_stage(
    ctx: PipelineContext, job: PipelineJob, session: Session
) -> dict[str, Any]:
    project = session.get(VideoProject, job.project_id)
    if project_claims(session, project):
        return {"skipped": True}
    dossier = session.scalar(
        select(Dossier).where(
            Dossier.project_id == project.id, Dossier.run == project.pipeline_run
        )
    )
    if dossier is None:
        raise StageError(
            "dossier missing", code="missing_dossier", module=__name__, retryable=False
        )
    sources = accepted_sources(session, project)
    by_id = {s.id: s for s in sources}
    result = ctx.llm.call(
        "claim_extraction",
        prompts.claims_prompt(dossier.content, [source_dict(s) for s in sources]),
        schema=ClaimsOut,
        system=prompts.CLAIMS_SYSTEM,
        project_id=project.id,
        channel_id=project.channel_id,
        stage=job.stage,
        session=session,
        max_tokens=12000,
        metadata={
            "source_ids": [s.id for s in sources],
            "primary_source_ids": [s.id for s in sources if s.is_primary],
        },
    )
    out: ClaimsOut = result.parsed
    created = 0
    for item in out.claims:
        supporting = [i for i in item.supporting_source_ids if i in by_id]
        evidence = [e.model_dump() for e in item.evidence if e.source_id in by_id]
        for e in evidence:
            if e["source_id"] not in supporting:
                supporting.append(e["source_id"])
        claim = ResearchClaim(
            project_id=project.id,
            run=project.pipeline_run,
            text=item.text.strip(),
            kind=ClaimKind(item.kind),
            importance=ClaimImportance(item.importance),
            confidence=item.confidence,
            evidence=evidence,
            contradicting_source_ids=[
                i for i in item.contradicting_source_ids if i in by_id
            ],
            geographic_scope=item.geographic_scope or None,
            time_period=item.time_period or None,
            entities=item.entities,
            notes=item.notes or None,
            verification_status=VerificationStatus.unverified,
        )
        claim.sources = [by_id[i] for i in supporting]
        if numeric_tokens(claim.text) and not evidence:
            claim.verification_status = VerificationStatus.unsupported
            claim.notes = (
                claim.notes + " | " if claim.notes else ""
            ) + "numeric claim without evidence fragment"
        session.add(claim)
        created += 1
    session.flush()
    return {"claims": created, "cost_eur": result.cost_eur}


def _apply_rules(claim: ResearchClaim, verdict: str) -> VerificationStatus:
    status = VerificationStatus(verdict)
    has_evidence = bool(claim.evidence)
    primary = any(s.is_primary for s in claim.sources)
    independent = len({s.domain for s in claim.sources}) >= 2
    if numeric_tokens(claim.text) and not has_evidence:
        return VerificationStatus.unsupported
    if claim.contradicting_source_ids and status == VerificationStatus.supported:
        return VerificationStatus.disputed
    if status == VerificationStatus.supported and claim.importance in (
        ClaimImportance.critical,
        ClaimImportance.high,
    ):
        if not primary and not independent:
            return (
                VerificationStatus.weak
            )  # important claim on a single secondary source
    if (
        claim.kind in (ClaimKind.opinion, ClaimKind.inference)
        and status == VerificationStatus.supported
    ):
        # Opinions/inferences are never "facts": keep them usable but hedged.
        return VerificationStatus.weak
    return status


def claims_verify_stage(
    ctx: PipelineContext, job: PipelineJob, session: Session
) -> dict[str, Any]:
    project = session.get(VideoProject, job.project_id)
    claims = [
        c
        for c in project_claims(session, project)
        if c.verification_status == VerificationStatus.unverified
    ]
    if not claims:
        return {"skipped": True}
    sources = accepted_sources(session, project)
    source_payload = [source_dict(s) for s in sources]
    cost = 0.0
    verdicts: dict[str, tuple[str, str]] = {}
    for i in range(0, len(claims), CHUNK):
        chunk = claims[i : i + CHUNK]
        result = ctx.llm.call(
            "fact_check",
            prompts.fact_check_prompt([claim_dict(c) for c in chunk], source_payload),
            schema=FactCheckOut,
            system=prompts.FACT_CHECK_SYSTEM,
            project_id=project.id,
            channel_id=project.channel_id,
            stage=job.stage,
            session=session,
            max_tokens=6000,
            metadata={"claim_ids": [c.id for c in chunk]},
        )
        cost += result.cost_eur
        for v in result.parsed.verdicts:
            verdicts[v.claim_id] = (v.status, v.reason)
    counts: dict[str, int] = {}
    for claim in claims:
        verdict, reason = verdicts.get(
            claim.id, ("unsupported", "no verdict returned by fact-checker")
        )
        claim.verification_status = _apply_rules(claim, verdict)
        claim.verified = claim.verification_status == VerificationStatus.supported
        claim.verification_note = reason
        counts[claim.verification_status.value] = (
            counts.get(claim.verification_status.value, 0) + 1
        )
    session.flush()
    return {"verified": counts, "cost_eur": cost}
