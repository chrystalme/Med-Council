"""Intake stage — generate patient follow-up questions.

Stage 1 of the council pipeline. Takes a free-text symptoms blob and
returns four numbered follow-up questions to refine the workup.
"""

from __future__ import annotations

from typing import Optional

from agents import InputGuardrailTripwireTriggered
from fastapi import APIRouter, Depends, HTTPException, Response

from ..agent_runtime import (
    format_intake_questions_for_api,
    resolve_for_request,
    run_agent,
    traced_workflow,
)
from ..auth import AuthUser, current_user_maybe_required
from ..council import intake_agent
from ..council_schemas import PatientSymptomsIn
from ..helpers import utc_now  # noqa: F401  # placeholder for future call sites

router = APIRouter()


@router.post("/api/intake/followup")
async def intake_followup(
    req: PatientSymptomsIn,
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    model_slug = resolve_for_request(req, user, response)
    try:
        with traced_workflow(
            "Intake Follow-up Questions",
            metadata={"stage": "1-intake", "symptoms": (req.symptoms or "")[:120]},
        ):
            raw_text = await run_agent(
                intake_agent,
                f"Patient self-reports: {req.symptoms}",
                model=model_slug,
            )
    except InputGuardrailTripwireTriggered as e:
        info = e.guardrail_result.output.output_info if e.guardrail_result.output else {}
        raise HTTPException(
            status_code=422,
            detail={
                "error": "non_medical_input",
                "message": (
                    "This service is designed for medical questions only. "
                    "Please describe a health concern, symptom, or medical situation."
                ),
                "reasoning": info.get("reasoning", ""),
            },
        ) from e
    try:
        return {"questions": format_intake_questions_for_api(raw_text)}
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
