"""Export the editorial package of a project as reproducible files.

The database is the source of truth; these files are derived exports written
to ``<storage_dir>/projects/<project_id>/``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mpt2.editorial.angles import options_for
from mpt2.editorial.context import claim_dict, project_claims, source_dict
from mpt2.editorial.script import current_script
from mpt2.editorial.storyboard import current_storyboard
from mpt2.models import (
    CostEntry,
    Dossier,
    LLMCall,
    QualityCheck,
    ResearchPlan,
    ResearchSource,
    SearchQuery,
    VideoProject,
)
from mpt2.models.enums import OptionKind
from mpt2.settings import Settings

ARTIFACT_FILES = (
    "research-plan.json",
    "search-log.json",
    "sources.json",
    "research.json",
    "research.md",
    "claims.json",
    "angles.json",
    "hooks.json",
    "script.json",
    "script.md",
    "storyboard.json",
    "editorial-qc.json",
    "cost-report.json",
)


def _dump(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def project_dir(settings: Settings, project: VideoProject) -> Path:
    return Path(settings.storage_dir) / "projects" / project.id


def cost_report(session: Session, project: VideoProject) -> dict[str, Any]:
    calls = session.scalars(
        select(LLMCall)
        .where(LLMCall.project_id == project.id)
        .order_by(LLMCall.created_at)
    ).all()
    entries = session.scalars(
        select(CostEntry).where(CostEntry.project_id == project.id)
    ).all()
    by_task: dict[str, dict[str, float]] = {}
    for c in calls:
        t = by_task.setdefault(
            c.task,
            {
                "calls": 0,
                "cache_hits": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "web_searches": 0,
                "cost_eur": 0.0,
                "errors": 0,
                "blocked": 0,
            },
        )
        t["calls"] += 1
        t["cache_hits"] += int(c.cache_hit)
        t["tokens_in"] += c.tokens_in
        t["tokens_out"] += c.tokens_out
        t["web_searches"] += c.web_search_requests
        t["cost_eur"] += c.cost_eur
        t["errors"] += int(c.status.value == "error")
        t["blocked"] += int(c.status.value == "blocked")
    by_model: dict[str, float] = {}
    for c in calls:
        by_model[c.model] = by_model.get(c.model, 0.0) + c.cost_eur
    return {
        "project_id": project.id,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "total_cost_eur": round(sum(e.est_cost_eur for e in entries), 6),
        "total_cost_usd": round(sum(c.cost_usd for c in calls), 6),
        "llm_calls": len(calls),
        "cache_hits": sum(1 for c in calls if c.cache_hit),
        "web_searches": sum(c.web_search_requests for c in calls),
        "tokens_in": sum(c.tokens_in for c in calls),
        "tokens_out": sum(c.tokens_out for c in calls),
        "by_task": {
            k: {
                kk: (round(vv, 6) if isinstance(vv, float) else vv)
                for kk, vv in v.items()
            }
            for k, v in by_task.items()
        },
        "by_model_eur": {k: round(v, 6) for k, v in by_model.items()},
        "cost_entries": [
            {
                "provider": e.provider,
                "stage": e.stage,
                "units": e.units,
                "unit_type": e.unit_type.value,
                "est_cost_eur": e.est_cost_eur,
                "created_at": e.created_at,
            }
            for e in entries
        ],
        "note": "Costs are estimates computed from token usage and the configured price table; reconcile with the Anthropic Console.",
    }


def render_research_md(
    project: VideoProject,
    dossier: dict[str, Any],
    sources: list[dict[str, Any]],
    researched_at: datetime | None,
) -> str:
    def ids(x: list[str]) -> str:
        return ", ".join(f"[{i[:8]}]" for i in x)

    lines = [
        f"# Research dossier: {project.title}",
        "",
        f"Topic: {project.topic}  ",
        f"Research date: {researched_at.isoformat() if researched_at else 'n/a'}  ",
        f"Sources: {len(sources)}",
        "",
        "## Executive summary",
        "",
        dossier.get("executive_summary", ""),
        "",
    ]
    lines += [
        "## Title assessment",
        "",
        dossier.get("title_assessment", "") or "n/a",
        "",
        "## Key facts",
        "",
    ]
    for f in dossier.get("key_facts", []):
        geo = (
            f" ({f.get('geography')}, {f.get('period')})"
            if f.get("geography") or f.get("period")
            else ""
        )
        lines.append(f"- {f['statement']}{geo} {ids(f.get('source_ids', []))}")
    if dossier.get("timeline"):
        lines += ["", "## Timeline", ""]
        for t in dossier["timeline"]:
            lines.append(
                f"- **{t['date']}**: {t['event']} {ids(t.get('source_ids', []))}"
            )
    if dossier.get("figures"):
        lines += [
            "",
            "## Figures",
            "",
            "| Label | Value | Unit | Geography | Period | Sources |",
            "|---|---|---|---|---|---|",
        ]
        for fg in dossier["figures"]:
            lines.append(
                f"| {fg['label']} | {fg['value']} | {fg.get('unit', '')} | {fg.get('geography', '')} | {fg.get('period', '')} | {ids(fg.get('source_ids', []))} |"
            )
    if dossier.get("contradictions"):
        lines += ["", "## Contradictory perspectives", ""]
        for c in dossier["contradictions"]:
            lines.append(
                f"- **{c['topic']}**: A) {c['position_a']} {ids(c.get('source_ids_a', []))} vs B) {c['position_b']} {ids(c.get('source_ids_b', []))}. {c.get('assessment', '')}"
            )
    for key, title in (
        ("gaps", "Gaps"),
        ("risks", "Risks"),
        ("discarded_hypotheses", "Discarded hypotheses"),
    ):
        if dossier.get(key):
            lines += ["", f"## {title}", ""] + [f"- {x}" for x in dossier[key]]
    lines += ["", "## Sources", ""]
    for s in sources:
        tag = "PRIMARY" if s.get("primary") else "secondary"
        lines.append(
            f"- [{s['id'][:8]}] {s['title']} — {s['domain']} ({tag}, {s.get('date') or 'date n/a'}) <{s['url']}>"
        )
    return "\n".join(lines) + "\n"


def render_script_md(script, claims_by_id: dict[str, Any]) -> str:
    lines = [
        f"# {script.title}",
        "",
        f"Words: {script.word_count} · Estimated duration: {script.est_duration_s / 60:.1f} min · Status: {getattr(script.status, 'value', script.status)}",
        "",
    ]
    for sec in script.sections:
        role = getattr(sec.role, "value", sec.role)
        flag = " ⚠ needs verification" if sec.needs_verification else ""
        lines += [
            f"## {sec.position + 1}. {sec.title} ({role}, {sec.est_duration_s:.0f}s){flag}",
            "",
            sec.text,
            "",
        ]
        if sec.claim_ids:
            lines.append(
                "Claims: "
                + ", ".join(
                    f"[{cid[:8]}] {claims_by_id[cid]['text'][:80]}"
                    for cid in sec.claim_ids
                    if cid in claims_by_id
                )
            )
            lines.append("")
    return "\n".join(lines) + "\n"


def export_project(session: Session, settings: Settings, project: VideoProject) -> Path:
    out = project_dir(settings, project)
    out.mkdir(parents=True, exist_ok=True)
    run = project.pipeline_run
    plan = session.scalar(
        select(ResearchPlan).where(
            ResearchPlan.project_id == project.id, ResearchPlan.run == run
        )
    )
    _dump(
        out / "research-plan.json",
        {
            "project_id": project.id,
            "run": run,
            "title": project.title,
            "topic": project.topic,
            "plan": plan.content if plan else None,
        },
    )
    queries = session.scalars(
        select(SearchQuery)
        .where(SearchQuery.project_id == project.id, SearchQuery.run == run)
        .order_by(SearchQuery.created_at)
    ).all()
    _dump(
        out / "search-log.json",
        [
            {
                "id": q.id,
                "query": q.query,
                "purpose": q.purpose,
                "provider": q.provider,
                "status": q.status,
                "error": q.error,
                "result_count": q.result_count,
                "executed_queries": q.executed_queries,
                "cost_eur": q.cost_eur,
                "created_at": q.created_at,
                "results": q.results,
            }
            for q in queries
        ],
    )
    all_sources = session.scalars(
        select(ResearchSource)
        .where(ResearchSource.project_id == project.id, ResearchSource.run == run)
        .order_by(ResearchSource.created_at)
    ).all()
    sources_payload = [
        {
            **source_dict(s),
            "status": s.status.value,
            "rejection_reason": s.rejection_reason,
            "accessed_at": s.accessed_at,
            "original_url": s.url,
            "query_id": s.query_id,
        }
        for s in all_sources
    ]
    _dump(out / "sources.json", sources_payload)
    dossier = session.scalar(
        select(Dossier).where(Dossier.project_id == project.id, Dossier.run == run)
    )
    accepted_payload = [p for p in sources_payload if p["status"] == "accepted"]
    _dump(
        out / "research.json",
        {
            "project_id": project.id,
            "run": run,
            "researched_at": dossier.researched_at if dossier else None,
            "dossier": dossier.content if dossier else None,
            "sources": accepted_payload,
        },
    )
    (out / "research.md").write_text(
        render_research_md(
            project,
            dossier.content if dossier else {},
            accepted_payload,
            dossier.researched_at if dossier else None,
        ),
        encoding="utf-8",
    )
    claims = project_claims(session, project)
    claims_payload = [
        {
            **claim_dict(c),
            "used_in_script": c.used_in_script,
            "human_edited": c.human_edited,
            "verification_note": c.verification_note,
            "notes": c.notes,
        }
        for c in claims
    ]
    _dump(out / "claims.json", claims_payload)
    opts = options_for(session, project)

    def opt(o):
        return {
            "id": o.id,
            "kind": o.kind.value,
            "position": o.position,
            "title": o.title,
            "text": o.text,
            "scores": o.scores,
            "total_score": o.total_score,
            "rationale": o.rationale,
            "selected": o.selected,
            "selected_by": o.selected_by,
            "selection_reason": o.selection_reason,
        }

    _dump(
        out / "angles.json",
        {
            "angles": [opt(o) for o in opts if o.kind == OptionKind.angle],
            "structures": [opt(o) for o in opts if o.kind == OptionKind.structure],
        },
    )
    _dump(out / "hooks.json", [opt(o) for o in opts if o.kind == OptionKind.hook])
    script = current_script(session, project)
    claims_by_id = {c["id"]: c for c in claims_payload}
    if script:
        _dump(
            out / "script.json",
            {
                "id": script.id,
                "title": script.title,
                "run": run,
                "status": script.status.value,
                "word_count": script.word_count,
                "est_duration_s": script.est_duration_s,
                "angle_id": script.angle_id,
                "hook_id": script.hook_id,
                "structure_id": script.structure_id,
                "sections": [
                    {
                        "id": s.id,
                        "position": s.position,
                        "role": getattr(s.role, "value", s.role),
                        "title": s.title,
                        "text": s.text,
                        "claim_ids": s.claim_ids,
                        "source_ids": sorted(
                            {
                                sid
                                for cid in s.claim_ids
                                for sid in claims_by_id.get(cid, {}).get(
                                    "supporting_source_ids", []
                                )
                            }
                        ),
                        "word_count": s.word_count,
                        "est_duration_s": s.est_duration_s,
                        "needs_verification": s.needs_verification,
                        "human_edited": s.human_edited,
                    }
                    for s in script.sections
                ],
            },
        )
        (out / "script.md").write_text(
            render_script_md(script, claims_by_id), encoding="utf-8"
        )
    else:
        _dump(out / "script.json", None)
        (out / "script.md").write_text("(no script yet)\n", encoding="utf-8")
    sb = current_storyboard(session, project)
    _dump(
        out / "storyboard.json",
        {
            "id": sb.id,
            "aspect": sb.aspect,
            "total_est_duration_s": sb.total_est_duration_s,
            "scenes": [
                {
                    "id": s.id,
                    "position": s.position,
                    "section_id": s.section_id,
                    "narration": s.narration,
                    "est_duration_s": s.est_duration_s,
                    "narrative_goal": s.narrative_goal,
                    "visual_description": s.visual_description,
                    "asset_type": getattr(s.asset_type, "value", s.asset_type),
                    "search_terms": s.search_terms,
                    "on_screen_text": s.on_screen_text,
                    "chart_data": s.chart_data,
                    "motion": s.motion,
                    "transition": s.transition,
                    "claim_ids": s.claim_ids,
                    "source_ids": s.source_ids,
                    "copyright_risk": s.copyright_risk,
                    "fallback_visual": s.fallback_visual,
                    "priority": s.priority,
                    "human_edited": s.human_edited,
                }
                for s in sb.scenes
            ],
        }
        if sb
        else None,
    )
    qcs = session.scalars(
        select(QualityCheck).where(
            QualityCheck.project_id == project.id, QualityCheck.run == run
        )
    ).all()
    _dump(
        out / "editorial-qc.json",
        [
            {
                "id": q.id,
                "kind": q.kind,
                "passed": q.passed,
                "total_score": q.total_score,
                "checks": q.checks,
                "created_at": q.created_at,
            }
            for q in qcs
        ],
    )
    _dump(out / "cost-report.json", cost_report(session, project))
    return out
