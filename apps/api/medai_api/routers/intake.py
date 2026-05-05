"""Intake stage — generate patient follow-up questions.

Stage 1 of the council pipeline. Takes a free-text symptoms blob and
returns four numbered follow-up questions to refine the workup.
"""

from __future__ import annotations

import re
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


# Common openings that aren't medical content but also aren't off-topic
# attempts — patient hasn't said anything substantive yet.
_GREETING_RE = re.compile(
    r"^(hi|hello|hey|yo|hiya|howdy|sup|what'?s up|good (morning|afternoon|evening|day)|"
    r"test|testing|ok|okay|thanks|thank you)[!.?\s]*$",
    re.IGNORECASE,
)


def _looks_like_greeting(text: str) -> bool:
    """True if the input is short / greeting-like rather than substantive off-topic content.

    Used to split the medical-topic guardrail trip into two response shapes:
    short noise gets a friendly nudge to describe symptoms; longer off-topic
    content (jailbreak attempts, recipe requests, anything that's actively
    leading the LLM somewhere else) gets the firm "medical questions only"
    message instead.
    """
    s = (text or "").strip()
    if not s:
        return True
    if len(s) <= 20:
        return True
    return bool(_GREETING_RE.match(s))


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
        # Medical-topic classifier rejected the input. Always return 200 (so
        # the frontend doesn't show "Intake failed (HTTP 422)" — that's
        # confusing UX for a patient who just said "Hi"). Two messages,
        # split on whether the input is a benign greeting or substantive
        # off-topic content (cooking recipe, jailbreak attempt, etc.).
        info = e.guardrail_result.output.output_info if e.guardrail_result.output else {}
        if _looks_like_greeting(req.symptoms):
            message = (
                "I am your medical intake Agent, kindly give me your "
                "medical symptoms for analysis."
            )
        else:
            message = (
                "This service is designed for medical questions only. "
                "Please describe a health concern, symptom, or medical situation."
            )
        return {
            "questions": [],
            "needs_symptoms": True,
            "message": message,
            "reasoning": info.get("reasoning", ""),
        }
    try:
        return {"questions": format_intake_questions_for_api(raw_text)}
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
