"""Stage 3 — physician council assessments.

Two routes share the same agent-call shape (run a specialist agent over
the case + colleague assessments and return its text):

  POST /api/council/specialist  — uses `specialist_id` (back-compat shape)
  POST /api/council/physician   — uses `physician_id` (frontend naming)

The physician variant additionally injects per-case attachments into the
prompt when `case_id` is supplied.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response

from agent_runtime import resolve_for_request, run_agent, traced_workflow
from auth import AuthUser, current_user_maybe_required
from case_context import attachment_block_for_case, retrieve_patient_context
from council import ALL_SPECIALIST_IDS, SPECIALIST_AGENTS, SPECIALIST_META
from helpers import cases_user_id
from schemas import PhysicianIn, SpecialistIn

router = APIRouter()


def _council_context_block(council_context: str) -> str:
    t = (council_context or "").strip()
    if not t:
        return ""
    return f"\n\nDeliberation lead framing (use alongside the chart):\n{t}"


@router.post("/api/council/specialist")
async def council_specialist(
    req: SpecialistIn,
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    model_slug = resolve_for_request(req, user, response)
    if req.specialist_id not in SPECIALIST_AGENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown specialist_id '{req.specialist_id}'. Valid: {ALL_SPECIALIST_IDS}",
        )

    prior_block = ""
    if req.prior_assessments:
        prior_block = "\n\nColleague assessments (read carefully before responding):\n" + "\n\n".join(
            f"--- {a['name']} ({a['specialty']}) ---\n{a['assessment']}"
            for a in req.prior_assessments
        )

    ctx = _council_context_block(req.council_context)
    memory = retrieve_patient_context(cases_user_id(user), req.symptoms)
    prompt = (
        f"Patient symptoms: {req.symptoms}\n\n"
        f"Patient follow-up responses: {req.followup_answers}"
        f"{ctx}"
        f"{prior_block}"
        + (f"\n\n{memory}" if memory else "")
    )

    specialist_name = SPECIALIST_META[req.specialist_id]["name"]
    with traced_workflow(
        f"Specialist Assessment: {specialist_name}",
        metadata={
            "stage": "3-council",
            "specialist_id": req.specialist_id,
            "specialist_name": specialist_name,
            "prior_assessment_count": len(req.prior_assessments),
            "symptoms": (req.symptoms or "")[:120],
        },
    ):
        assessment = await run_agent(SPECIALIST_AGENTS[req.specialist_id], prompt, model=model_slug)
    return {
        "specialist": {"id": req.specialist_id, **SPECIALIST_META[req.specialist_id]},
        "assessment": assessment,
    }


@router.post("/api/council/physician")
async def council_physician(
    req: PhysicianIn,
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    """Alias for council_specialist to match frontend naming (physician_id instead of specialist_id)"""
    model_slug = resolve_for_request(req, user, response)
    if req.physician_id not in SPECIALIST_AGENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown physician_id '{req.physician_id}'. Valid: {ALL_SPECIALIST_IDS}",
        )

    prior_block = ""
    if req.prior_assessments:
        prior_block = "\n\nColleague assessments (read carefully before responding):\n" + "\n\n".join(
            f"--- {a['name']} ({a['specialty']}) ---\n{a['assessment']}"
            for a in req.prior_assessments
        )

    ctx = _council_context_block(req.council_context)
    user_id = cases_user_id(user)
    memory = retrieve_patient_context(user_id, req.symptoms)
    attachments_block = attachment_block_for_case(req.case_id, user_id) if req.case_id else ""
    prompt = (
        f"Patient symptoms: {req.symptoms}\n\n"
        f"Patient follow-up responses: {req.followup_answers}"
        f"{ctx}"
        f"{prior_block}"
        + (f"\n\n{attachments_block}" if attachments_block else "")
        + (f"\n\n{memory}" if memory else "")
    )

    specialist_name = SPECIALIST_META[req.physician_id]["name"]
    with traced_workflow(
        f"Specialist Assessment: {specialist_name}",
        metadata={
            "stage": "3-council",
            "specialist_id": req.physician_id,
            "specialist_name": specialist_name,
            "prior_assessment_count": len(req.prior_assessments),
            "symptoms": (req.symptoms or "")[:120],
        },
    ):
        assessment = await run_agent(SPECIALIST_AGENTS[req.physician_id], prompt, model=model_slug)
    return {
        "specialist": {"id": req.physician_id, **SPECIALIST_META[req.physician_id]},
        "assessment": assessment,
    }
