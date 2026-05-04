"""Stages 5 + 6 — consensus diagnosis and coordinated treatment plan.

Bundled together because both:
  * pull memory + attachments via case_context helpers,
  * run a single agent over the same kind of payload (symptoms +
    assessments + prior context), and
  * are always called in sequence by the frontend.

Consensus also fires a fire-and-forget on-call escalation when the
diagnosis urgency warrants it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response

from agent_runtime import parse_json, resolve_for_request, run_agent, traced_workflow
from auth import AuthUser, current_user_maybe_required
from case_context import attachment_block_for_case, retrieve_patient_context
from council import consensus_agent, plan_agent
from escalation import maybe_escalate_oncall
from helpers import cases_user_id
from schemas import ConsensusIn, PlanIn

router = APIRouter()


@router.post("/api/consensus")
async def consensus(
    req: ConsensusIn,
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    model_slug = resolve_for_request(req, user, response)
    assessments_text = "\n\n".join(
        f"{a['name']} ({a['specialty']}):\n{a['assessment']}" for a in req.assessments
    )
    research_text = "\n".join(
        f"• {r.get('title','')} ({r.get('year','')}): {r.get('summary','')}"
        for r in req.research
    )
    user_id = cases_user_id(user)
    memory = retrieve_patient_context(user_id, req.symptoms)
    attachments_block = attachment_block_for_case(req.case_id, user_id) if req.case_id else ""
    prompt = (
        f"Patient symptoms: {req.symptoms}\n\n"
        f"Follow-up responses: {req.followup_answers}\n\n"
        f"Specialist assessments:\n{assessments_text}\n\n"
        f"Supporting research:\n{research_text}"
        + (f"\n\n{attachments_block}" if attachments_block else "")
        + (f"\n\n{memory}" if memory else "")
    )
    with traced_workflow(
        "Consensus: Integrating Multidisciplinary Assessment",
        metadata={
            "stage": "5-consensus",
            "assessment_count": len(req.assessments),
            "research_paper_count": len(req.research),
            "symptoms": (req.symptoms or "")[:120],
        },
    ):
        raw = await run_agent(consensus_agent, prompt, model=model_slug)

    try:
        data = parse_json(raw)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    if isinstance(data, dict):
        asyncio.create_task(
            asyncio.to_thread(
                maybe_escalate_oncall,
                consensus=data,
                symptoms=req.symptoms,
            )
        )

    return {"consensus": data}


@router.post("/api/plan")
async def plan(
    req: PlanIn,
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    model_slug = resolve_for_request(req, user, response)
    assessments_text = "\n\n".join(
        f"{a['name']} ({a['specialty']}):\n{a['assessment']}" for a in req.assessments
    )
    user_id = cases_user_id(user)
    memory = retrieve_patient_context(user_id, req.symptoms)
    attachments_block = attachment_block_for_case(req.case_id, user_id) if req.case_id else ""
    prompt = (
        f"Diagnosis: {json.dumps(req.consensus)}\n\n"
        f"Patient symptoms: {req.symptoms}\n\n"
        f"Follow-up responses: {req.followup_answers}\n\n"
        f"Specialist findings:\n{assessments_text}"
        + (f"\n\n{attachments_block}" if attachments_block else "")
        + (f"\n\n{memory}" if memory else "")
    )
    with traced_workflow(
        "Treatment Plan: Multi-Specialty Coordination",
        metadata={
            "stage": "6-plan",
            "assessment_count": len(req.assessments),
            "symptoms": (req.symptoms or "")[:120],
        },
    ):
        plan_text = await run_agent(plan_agent, prompt, model=model_slug)
    return {"plan": plan_text}
