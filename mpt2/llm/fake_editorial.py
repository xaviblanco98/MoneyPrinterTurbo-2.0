"""A scripted fake backend that produces schema-valid editorial outputs.

Used by the automated tests and by ``MPT2_LLM_BACKEND=fake`` offline runs.
Everything it produces is obviously synthetic (prefixed with [FAKE]); it
never pretends to be research. It reads ``request.metadata`` (ids the
handlers pass) so that references stay consistent across stages.
"""

from __future__ import annotations

import re
from typing import Any

from mpt2.llm.backend import BackendRequest

FAKE_NUMBERS = ["12 percent", "8 percent", "2,400 dollars", "2019", "3.5 billion"]


def _sentences(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text or "") if p.strip()]
    return parts or [text]


def _scores(seed: int) -> dict[str, float]:
    base = 5 + (seed % 5)
    return {
        "curiosity": min(10, base + 1),
        "clarity": base,
        "credibility": min(10, base + 2),
        "specificity": base,
        "originality": max(1, base - 1),
        "retention": base,
        "evidence_availability": min(10, base + 1),
        "clickbait_risk": max(0, 4 - seed % 4),
        "legal_risk": 1,
        "channel_fit": min(10, base + 1),
    }


class EditorialScripter:
    """Callable(request) -> dict for every editorial task."""

    def __init__(
        self, *, invalid_first: set[str] | None = None, target_words: int | None = None
    ):
        self.invalid_first = set(invalid_first or ())
        self.seen: dict[str, int] = {}
        self.target_words = target_words

    def __call__(self, request: BackendRequest) -> dict[str, Any] | str:
        task = request.metadata.get("task", "")
        self.seen[task] = self.seen.get(task, 0) + 1
        if (
            task in self.invalid_first
            and self.seen[task] == 1
            and not request.metadata.get("repair")
        ):
            return "this is not json at all"
        handler = getattr(self, f"task_{task}", None)
        if handler is None:
            return {"text": f"[FAKE] no scripted response for task {task}"}
        return handler(request)

    # ------------------------------------------------------------ tasks
    def task_research_plan(self, request: BackendRequest) -> dict[str, Any]:
        topics = [
            "margins",
            "financing",
            "products",
            "warranties",
            "service",
            "incentives",
            "inventory",
            "geography",
            "counter-examples",
        ]
        return {
            "central_question": "[FAKE] Where does the money really come from in this business, and does the working title hold?",
            "subquestions": [f"[FAKE] Subquestion about {t}" for t in topics],
            "required_information": [f"[FAKE] Data needed on {t}" for t in topics[:5]],
            "ideal_sources": [
                "[FAKE] regulator reports",
                "[FAKE] industry association statistics",
                "[FAKE] company filings",
            ],
            "misinformation_risks": ["[FAKE] outdated averages presented as current"],
            "sensitive_claims": ["[FAKE] claims about specific companies' practices"],
            "search_strategy": [
                {
                    "query": f"[FAKE] query {i} {t}",
                    "purpose": f"[FAKE] cover {t}",
                    "subquestion": t,
                    "perspective": "regulator" if i % 2 else "industry",
                }
                for i, t in enumerate(topics)
            ],
            "hypotheses_to_test": ["[FAKE] The title is wrong in some markets"],
            "title_could_be_wrong_if": [
                "[FAKE] financing income is smaller than service income"
            ],
        }

    def task_dossier(self, request: BackendRequest) -> dict[str, Any]:
        ids = list(request.metadata.get("source_ids", []))
        if not ids:
            ids = ["missing"]
        return {
            "executive_summary": "[FAKE] "
            + "Synthetic executive summary sentence. " * 12,
            "key_facts": [
                {
                    "statement": f"[FAKE] Key fact {i} with figure {FAKE_NUMBERS[i % len(FAKE_NUMBERS)]}.",
                    "source_ids": [ids[i % len(ids)]],
                    "geography": "US",
                    "period": "2024",
                }
                for i in range(6)
            ],
            "timeline": [
                {
                    "date": "2019",
                    "event": "[FAKE] Something happened",
                    "source_ids": [ids[0]],
                }
            ],
            "figures": [
                {
                    "label": "[FAKE] margin",
                    "value": "12",
                    "unit": "percent",
                    "geography": "US",
                    "period": "2024",
                    "source_ids": [ids[0]],
                }
            ],
            "contradictions": [
                {
                    "topic": "[FAKE] margin size",
                    "position_a": "12 percent",
                    "source_ids_a": [ids[0]],
                    "position_b": "8 percent",
                    "source_ids_b": [ids[-1]],
                    "assessment": "[FAKE] depends on segment",
                }
            ],
            "gaps": ["[FAKE] no EU data"],
            "risks": ["[FAKE] averages hide dispersion"],
            "discarded_hypotheses": ["[FAKE] dealers lose money on every car"],
            "title_assessment": "[FAKE] The evidence partly supports the title.",
        }

    def task_claim_extraction(self, request: BackendRequest) -> dict[str, Any]:
        ids = list(request.metadata.get("source_ids", [])) or ["missing"]
        primary = list(request.metadata.get("primary_source_ids", [])) or ids[:1]
        claims = []
        for i in range(8):
            num = FAKE_NUMBERS[i % len(FAKE_NUMBERS)]
            sid = primary[i % len(primary)] if i % 3 != 2 else ids[-1]
            claims.append(
                {
                    "text": f"[FAKE] Claim {i}: the figure is {num} in the reference year.",
                    "kind": "fact" if i % 4 else "estimate",
                    "importance": "critical"
                    if i == 0
                    else ("high" if i % 2 else "medium"),
                    "confidence": 0.8,
                    "supporting_source_ids": [sid],
                    "contradicting_source_ids": [],
                    "evidence": [
                        {
                            "source_id": sid,
                            "text": f"[FAKE] Synthetic evidence fragment stating {num}.",
                        }
                    ],
                    "geographic_scope": "US",
                    "time_period": "2024",
                    "entities": ["[FAKE] Example Corp"],
                    "notes": "",
                }
            )
        # one numeric claim with no evidence -> must end unsupported
        claims.append(
            {
                "text": "[FAKE] Unsupported claim says 99 percent.",
                "kind": "fact",
                "importance": "low",
                "confidence": 0.3,
                "supporting_source_ids": [],
                "contradicting_source_ids": [],
                "evidence": [],
                "geographic_scope": "",
                "time_period": "",
                "entities": [],
                "notes": "",
            }
        )
        return {"claims": claims}

    def task_fact_check(self, request: BackendRequest) -> dict[str, Any]:
        ids = request.metadata.get("claim_ids", [])
        return {
            "verdicts": [
                {
                    "claim_id": cid,
                    "status": "supported",
                    "reason": "[FAKE] evidence matches",
                }
                for cid in ids
            ],
            "general_notes": "[FAKE]",
        }

    def task_angles_hooks(self, request: BackendRequest) -> dict[str, Any]:
        cids = list(request.metadata.get("claim_ids", []))[:3]

        def opt(prefix: str, i: int) -> dict[str, Any]:
            return {
                "title": f"[FAKE] {prefix} {i}",
                "text": f"[FAKE] {prefix} option number {i} grounded in claims.",
                "scores": _scores(i),
                "rationale": "[FAKE] rationale",
                "claim_ids": cids,
            }

        return {
            "angles": [opt("Angle", i) for i in range(5)],
            "hooks": [opt("Hook", i) for i in range(10)],
            "structures": [opt("Structure", i) for i in range(3)],
            "recommended_angle": 1,
            "recommended_hook": 2,
            "recommended_structure": 0,
            "recommendation_rationale": "[FAKE] best balance of curiosity and evidence",
        }

    def task_script_write(self, request: BackendRequest) -> dict[str, Any]:
        claims = list(request.metadata.get("claims", []))
        target = self.target_words or int(request.metadata.get("target_words", 1000))
        roles = (
            ["hook", "promise", "context"]
            + ["development", "reveal"] * 4
            + ["conclusion", "cta"]
        )
        per_section = max(20, target // len(roles))
        sections = []
        for i, role in enumerate(roles):
            claim = claims[i % len(claims)] if claims else None
            filler = " ".join(
                ["[FAKE] synthetic narration sentence that carries the story forward."]
                * max(1, per_section // 9)
            )
            text = (
                f"{claim['text']} "
                if claim and role in ("development", "reveal")
                else ""
            ) + filler
            sections.append(
                {
                    "role": role,
                    "title": f"[FAKE] Section {i} {role}",
                    "text": text,
                    "claim_ids": [claim["id"]]
                    if claim and role in ("development", "reveal")
                    else [],
                    "notes": "",
                }
            )
        return {
            "title": "[FAKE] Script title",
            "sections": sections,
            "geography_note": "[FAKE] US vs EU differ",
        }

    def task_script_repair(self, request: BackendRequest) -> dict[str, Any]:
        sections = request.metadata.get("sections", [])
        claims = list(request.metadata.get("claims", []))
        fixed = []
        for sec in sections:
            text = re.sub(
                r"\d[\d,]*\.?\d*\s*(percent|%|billion|million|dollars)?",
                "",
                sec["text"],
            )
            fixed.append(
                {
                    "role": "development",
                    "title": sec["title"],
                    "text": text + " [FAKE repaired: unsourced numbers removed]",
                    "claim_ids": sec.get("claim_ids")
                    or ([claims[0]["id"]] if claims else []),
                    "notes": "repaired",
                }
            )
        return {"sections": fixed}

    def task_storyboard(self, request: BackendRequest) -> dict[str, Any]:
        text = request.metadata.get("section_text", "")
        cids = list(request.metadata.get("claim_ids", []))
        scenes = []
        for i, sentence in enumerate(_sentences(text)):
            words = len(sentence.split())
            scenes.append(
                {
                    "narration": sentence,
                    "est_duration_s": max(1.0, min(30.0, words / 2.5)),
                    "narrative_goal": "[FAKE] advance the story",
                    "visual_description": "[FAKE] concrete visual matching the sentence, e.g. a chart or archive photo",
                    "asset_type": "chart" if i % 3 == 0 else "stock",
                    "search_terms": ["[FAKE] term"],
                    "on_screen_text": "" if i % 2 else "[FAKE] 12%",
                    "chart_data": {
                        "chart_type": "bar",
                        "title": "[FAKE]",
                        "series": [{"label": "a", "value": 12}],
                        "claim_ids": cids[:1],
                    }
                    if i % 3 == 0
                    else None,
                    "motion": "slow zoom",
                    "transition": "cut",
                    "claim_ids": cids[:1],
                    "copyright_risk": "low",
                    "fallback_visual": "[FAKE] animated text",
                    "priority": "normal",
                }
            )
        return {"scenes": scenes}

    def task_editorial_qc(self, request: BackendRequest) -> dict[str, Any]:
        names = [
            "hook_quality",
            "clarity",
            "estimated_retention",
            "originality",
            "legal_risk",
            "copyright_risk",
            "policy_risk",
            "storyboard_coherence",
            "source_quality",
        ]
        return {
            "scores": [
                {"name": n, "score": 80, "rationale": "[FAKE] subjective assessment"}
                for n in names
            ],
            "editorial_issues": ["[FAKE] issue"],
            "strengths": ["[FAKE] strength"],
        }

    def task_web_search(self, request: BackendRequest) -> dict[str, Any]:
        return {"text": "[FAKE] search summary"}
