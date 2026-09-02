"""Research stages: plan -> search -> sources -> dossier."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from mpt2.contracts import SearchContext, SourceCandidate
from mpt2.editorial import prompts
from mpt2.editorial.context import (
    PipelineContext,
    accepted_sources,
    channel_dict,
    source_dict,
)
from mpt2.editorial.schemas import DossierOut, ResearchPlanOut
from mpt2.errors import StageError
from mpt2.models import (
    Dossier,
    PipelineJob,
    ResearchPlan,
    ResearchSource,
    SearchQuery,
    VideoProject,
    utcnow,
)
from mpt2.models.enums import SourceStatus
from mpt2.research.urls import (
    canonical_url,
    domain_of,
    is_allowed_listed,
    is_banned,
    looks_like_domain,
    looks_primary,
)

# Sub-topics every plan must cover can be passed per project in ``notes`` (JSON) or
# come from the channel; the pilot passes them explicitly via the API/CLI.
REQUIRED_TOPICS_KEY = "required_topics"


def _required_topics(project: VideoProject) -> list[str]:
    import json

    if project.notes:
        try:
            data = json.loads(project.notes)
            if isinstance(data, dict) and isinstance(
                data.get(REQUIRED_TOPICS_KEY), list
            ):
                return [str(t) for t in data[REQUIRED_TOPICS_KEY]]
        except json.JSONDecodeError:
            pass
    return []


# ------------------------------------------------------------------ plan


def research_plan_stage(
    ctx: PipelineContext, job: PipelineJob, session: Session
) -> dict[str, Any]:
    project = session.get(VideoProject, job.project_id)
    existing = session.scalar(
        select(ResearchPlan).where(
            ResearchPlan.project_id == project.id,
            ResearchPlan.run == project.pipeline_run,
        )
    )
    if existing is not None:
        return {"plan_id": existing.id, "skipped": True}
    channel = channel_dict(project.channel)
    result = ctx.llm.call(
        "research_plan",
        prompts.research_plan_prompt(
            project.topic, project.title, channel, _required_topics(project)
        ),
        schema=ResearchPlanOut,
        system=prompts.RESEARCH_PLAN_SYSTEM,
        project_id=project.id,
        channel_id=project.channel_id,
        stage=job.stage,
        session=session,
        max_tokens=6000,
    )
    plan: ResearchPlanOut = result.parsed
    limit = ctx.settings.research_max_searches_per_project
    content = plan.model_dump()
    content["search_strategy"] = content["search_strategy"][:limit]
    row = ResearchPlan(project_id=project.id, run=project.pipeline_run, content=content)
    session.add(row)
    session.flush()
    return {
        "plan_id": row.id,
        "queries": len(content["search_strategy"]),
        "cost_eur": result.cost_eur,
    }


# ---------------------------------------------------------------- search


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%dT%H:%M:%S", "%Y"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def _upsert_source(
    session: Session,
    project: VideoProject,
    candidate: SourceCandidate,
    query: SearchQuery,
    channel: dict[str, Any],
    settings,
) -> ResearchSource:
    canon = canonical_url(candidate.url)
    domain = candidate.domain or domain_of(candidate.url)
    row = session.scalar(
        select(ResearchSource).where(
            ResearchSource.project_id == project.id,
            ResearchSource.run == project.pipeline_run,
            ResearchSource.canonical_url == canon,
        )
    )
    snippets = [
        s
        for s in (
            candidate.snippets or ([candidate.snippet] if candidate.snippet else [])
        )
        if s
    ]
    if row is None:
        allowed_listed = is_allowed_listed(domain, channel["allowed_sources"])
        primary = looks_primary(candidate.url, domain) or allowed_listed
        row = ResearchSource(
            project_id=project.id,
            run=project.pipeline_run,
            url=candidate.url,
            canonical_url=canon,
            domain=domain,
            title=candidate.title or candidate.url,
            author=candidate.author,
            publisher=candidate.publisher or domain,
            published_at=_parse_date(candidate.published_at or candidate.page_age),
            accessed_at=utcnow(),
            snippets=snippets,
            is_primary=primary,
            source_type="primary" if primary else "secondary",
            reliability=5 if allowed_listed else (4 if primary else 3),
            status=SourceStatus.candidate,
            query_id=query.id,
        )
        session.add(row)
        session.flush()
    else:
        merged = list(row.snippets or [])
        for s in snippets:
            if s not in merged:
                merged.append(s)
        row.snippets = merged
    # Policy checks (re-evaluated on every merge)
    if is_banned(domain, channel["banned_sources"]):
        row.status, row.rejection_reason = SourceStatus.rejected, "banned domain"
    elif settings.research_domain_policy == "restrict" and not is_allowed_listed(
        domain, channel["allowed_sources"]
    ):
        row.status, row.rejection_reason = (
            SourceStatus.rejected,
            "domain not in allowed list (restrict policy)",
        )
    elif sum(len(s) for s in row.snippets or []) < settings.research_min_snippet_chars:
        row.status, row.rejection_reason = (
            SourceStatus.rejected,
            "insufficient content (no usable excerpt)",
        )
    else:
        row.status, row.rejection_reason = SourceStatus.accepted, None
    return row


def research_search_stage(
    ctx: PipelineContext, job: PipelineJob, session: Session
) -> dict[str, Any]:
    project = session.get(VideoProject, job.project_id)
    plan = session.scalar(
        select(ResearchPlan).where(
            ResearchPlan.project_id == project.id,
            ResearchPlan.run == project.pipeline_run,
        )
    )
    if plan is None:
        raise StageError(
            "research plan missing",
            code="missing_plan",
            module=__name__,
            retryable=False,
        )
    channel = channel_dict(project.channel)
    strategy = list(plan.content.get("search_strategy", []))[
        : ctx.settings.research_max_searches_per_project
    ]
    done_queries = {
        q.query
        for q in session.scalars(
            select(SearchQuery).where(
                SearchQuery.project_id == project.id,
                SearchQuery.run == project.pipeline_run,
            )
        ).all()
    }
    context = SearchContext(
        session=session,
        project_id=project.id,
        channel_id=project.channel_id,
        stage=job.stage,
        blocked_domains=[d for d in channel["banned_sources"] if looks_like_domain(d)],
        allowed_domains=[d for d in channel["allowed_sources"] if looks_like_domain(d)]
        if ctx.settings.research_domain_policy == "restrict"
        else [],
        country=ctx.settings.research_user_country or channel.get("country"),
    )
    executed = 0
    failed = 0
    for item in strategy:
        query = str(item.get("query", "")).strip()
        if not query or query in done_queries:
            continue
        result = ctx.research.search(
            query, language=project.language, context=context, limit=10
        )
        log = SearchQuery(
            project_id=project.id,
            run=project.pipeline_run,
            provider=result.provider,
            query=query,
            purpose=str(item.get("purpose", "")),
            language=project.language,
            status="error" if result.error and not result.candidates else "ok",
            error=result.error,
            result_count=len(result.candidates),
            results=[c.model_dump() for c in result.candidates],
            executed_queries=result.executed_queries,
            cost_eur=result.cost_eur,
        )
        session.add(log)
        session.flush()
        for candidate in result.candidates:
            try:
                _upsert_source(session, project, candidate, log, channel, ctx.settings)
            except ValueError as exc:  # malformed URL
                logger.warning(f"skipping malformed source {candidate.url!r}: {exc}")
        # Commit per query so a crash never loses completed searches (resumable).
        session.commit()
        executed += 1
        if log.status == "error":
            failed += 1
        done_queries.add(query)

    # Cap the accepted set: primary first, then the ones with most evidence.
    accepted = accepted_sources(session, project)
    if len(accepted) > ctx.settings.research_max_sources:
        ranked = sorted(
            accepted, key=lambda s: (not s.is_primary, -len(s.snippets or []))
        )
        for extra in ranked[ctx.settings.research_max_sources :]:
            extra.status, extra.rejection_reason = (
                SourceStatus.rejected,
                "over source cap (lower priority)",
            )
        accepted = ranked[: ctx.settings.research_max_sources]
    total_queries = len(done_queries)
    if len(accepted) < 3:
        raise StageError(
            f"only {len(accepted)} usable sources after {total_queries} searches ({failed} failed); "
            "refusing to continue without real sources",
            code="insufficient_sources",
            module=__name__,
            retryable=False,
        )
    return {
        "skipped": executed == 0,
        "queries_executed": executed,
        "queries_total": total_queries,
        "queries_failed": failed,
        "accepted_sources": len(accepted),
    }


# --------------------------------------------------------------- dossier


def _query_summaries(session: Session, project: VideoProject) -> list[dict[str, str]]:
    rows = session.scalars(
        select(SearchQuery).where(
            SearchQuery.project_id == project.id,
            SearchQuery.run == project.pipeline_run,
            SearchQuery.status == "ok",
        )
    ).all()
    out = []
    for q in rows:
        summary = ""
        # summaries are kept out of the DB payloads except the short text in results; keep prompt lean
        out.append({"query": q.query, "purpose": q.purpose or "", "summary": summary})
    return out


def research_dossier_stage(
    ctx: PipelineContext, job: PipelineJob, session: Session
) -> dict[str, Any]:
    project = session.get(VideoProject, job.project_id)
    existing = session.scalar(
        select(Dossier).where(
            Dossier.project_id == project.id, Dossier.run == project.pipeline_run
        )
    )
    if existing is not None:
        return {"dossier_id": existing.id, "skipped": True}
    plan = session.scalar(
        select(ResearchPlan).where(
            ResearchPlan.project_id == project.id,
            ResearchPlan.run == project.pipeline_run,
        )
    )
    sources = accepted_sources(session, project)
    if len(sources) < 3:
        raise StageError(
            "not enough accepted sources for a dossier",
            code="insufficient_sources",
            module=__name__,
            retryable=False,
        )
    channel = channel_dict(project.channel)
    result = ctx.llm.call(
        "dossier",
        prompts.dossier_prompt(
            project.topic,
            project.title,
            plan.content if plan else {},
            [source_dict(s) for s in sources],
            channel,
        ),
        schema=DossierOut,
        system=prompts.DOSSIER_SYSTEM,
        project_id=project.id,
        channel_id=project.channel_id,
        stage=job.stage,
        session=session,
        max_tokens=12000,
        metadata={"source_ids": [s.id for s in sources]},
    )
    dossier: DossierOut = result.parsed
    valid_ids = {s.id for s in sources}
    content = dossier.model_dump()
    dropped = 0
    for key in ("key_facts", "figures", "timeline"):
        kept = []
        for item in content.get(key, []):
            ids = [i for i in item.get("source_ids", []) if i in valid_ids]
            if key != "timeline" and not ids:
                dropped += 1
                continue
            item["source_ids"] = ids
            kept.append(item)
        content[key] = kept
    for c in content.get("contradictions", []):
        c["source_ids_a"] = [i for i in c.get("source_ids_a", []) if i in valid_ids]
        c["source_ids_b"] = [i for i in c.get("source_ids_b", []) if i in valid_ids]
    content["validation"] = {
        "dropped_unsourced_items": dropped,
        "source_count": len(sources),
        "primary_sources": sum(1 for s in sources if s.is_primary),
    }
    row = Dossier(
        project_id=project.id,
        run=project.pipeline_run,
        content=content,
        researched_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return {
        "dossier_id": row.id,
        "facts": len(content["key_facts"]),
        "dropped": dropped,
        "cost_eur": result.cost_eur,
    }
