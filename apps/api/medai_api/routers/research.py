"""Stage 4 — research / evidence-based paper selection.

Runs the research agent over symptoms + assessments, parses the JSON
papers payload, and falls back to a direct PubMed search if the model's
output lacks URLs.
"""

from __future__ import annotations

import logging
from typing import Optional

from agents.tracing import custom_span
from fastapi import APIRouter, Depends, Response

from ..agent_runtime import resolve_for_request, run_agent, traced_workflow
from ..auth import AuthUser, current_user_maybe_required
from ..council import research_agent
from ..council_schemas import parse_research_papers
from ..external.pubmed import search_papers
from ..schemas import ResearchIn

log = logging.getLogger("medai.research")

router = APIRouter()


@router.post("/api/research")
async def research(
    req: ResearchIn,
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    model_slug = resolve_for_request(req, user, response)
    assessments_text = "\n\n".join(
        f"{a['name']} ({a['specialty']}):\n{a['assessment']}" for a in req.assessments
    )
    prompt = (
        f"Patient symptoms: {req.symptoms}\n\n"
        f"Follow-up responses: {req.followup_answers}\n\n"
        f"Team assessments:\n{assessments_text}"
    )
    with traced_workflow(
        "Research: Evidence-Based Paper Selection",
        metadata={
            "stage": "4-research",
            "assessment_count": len(req.assessments),
            "symptoms": (req.symptoms or "")[:120],
        },
    ):
        raw = await run_agent(research_agent, prompt, model=model_slug)

    with custom_span("parse_research_papers", data={"source": "model_output"}):
        papers, parse_warning = parse_research_papers(raw)

    # Failsafe: if the model didn't return a usable papers array (or produced narrative-only output),
    # fetch real PubMed links based on the case text so the UI always has actionable references.
    has_any_links = any(bool((p or {}).get("url")) for p in (papers or []))
    if not has_any_links:
        try:
            with custom_span("pubmed_fallback_search", data={"reason": "no_urls_in_model_output"}):
                pubmed_term = f"{req.symptoms}\n{req.followup_answers}\n{assessments_text}"
                pubmed_papers = search_papers(pubmed_term, retmax=4)
            if pubmed_papers:
                papers = pubmed_papers
                parse_warning = (
                    (parse_warning + " " if parse_warning else "")
                    + "Recovered PubMed links via direct search fallback."
                )
        except Exception as exc:
            # NCBI rate-limits + network timeouts shouldn't fail the whole research stage.
            log.warning("pubmed fallback search failed: %s", exc)
            parse_warning = (
                (parse_warning + " " if parse_warning else "")
                + "PubMed fallback unavailable."
            )

    return {"papers": papers, "parse_warning": parse_warning}
