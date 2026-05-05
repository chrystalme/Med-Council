"""Stage 7 — patient-facing message + Q&A follow-up.

`/api/message` runs the message_agent over the consensus + plan and
emits a patient-friendly summary; if the output guardrail trips on
the disclaimer check, the request retries once with an explicit
corrective hint before bubbling. Other guardrail subcodes go straight
to the global 422 handler.

`/api/message/followup` answers patient questions after the message
was delivered, with the original consensus / plan threaded into the
prompt for context.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from agents import OutputGuardrailTripwireTriggered
from fastapi import APIRouter, Depends, Response

from agent_runtime import resolve_for_request, run_agent, traced_workflow
from auth import AuthUser, current_user_maybe_required
from council import followup_qa_agent, message_agent
from schemas import MessageIn, PatientFollowUpIn

log = logging.getLogger("medai.message")

router = APIRouter()


_DISCLAIMER_RETRY_HINT = (
    "\n\n---\n"
    "CRITICAL CORRECTION REQUIRED — your previous response was rejected by "
    "the output guardrail. The closing sentences MUST contain ALL of:\n"
    "  (1) a reference to this being an AI advisory system (or AI guidance),\n"
    "  (2) the words 'physician' / 'doctor' / 'clinician' / 'healthcare provider',\n"
    "  (3) one of:\n"
    "      • a verb like 'consult', 'see', 'seek', 'speak', 'talk to', 'discuss', or 'follow up' with a clinician, OR\n"
    "      • a phrase noting the AI is 'not a substitute / replacement / alternative' for clinical care.\n"
    "Re-write the entire patient message so the FINAL sentence(s) clearly "
    "satisfy all three. Do not add any prefatory note; produce the corrected "
    "message directly."
)


@router.post("/api/message")
async def patient_message(
    req: MessageIn,
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    model_slug = resolve_for_request(req, user, response)
    prompt = (
        f"Primary diagnosis: {req.consensus.get('primaryDiagnosis')} "
        f"(confidence {req.consensus.get('confidence')}%, {req.consensus.get('urgency')} urgency)\n"
        f"ICD code: {req.consensus.get('icdCode', '')}\n"
        f"Prognosis: {req.consensus.get('prognosis')}\n"
        f"Key findings: {req.consensus.get('keyFindings')}\n\n"
        f"Treatment plan:\n{req.plan}\n\n"
        f"Original patient symptoms: {req.symptoms}"
    )
    # When the message guardrail trips on the disclaimer check, give the model
    # one more shot with an explicit corrective hint instead of hard-failing.
    # Disclaimer drift is the single most common message-stage trip and a
    # second pass with the failure quoted back almost always succeeds. Other
    # subcodes (e.g. message_introduces_unknown_diagnosis) are harder to
    # self-correct, so they bubble straight to the global 422 handler.
    #
    # Note: the retry attempt CAN trip a different guardrail (e.g. the
    # diagnosis-hallucination check under MESSAGE_HALLUCINATION_CHECK=1) — in
    # that case the second-attempt 422 carries the new subcode, not
    # "disclaimer_missing". Surfaced that way intentionally so callers see the
    # actual failure mode.

    retried = False
    retry_reason: str | None = None
    with traced_workflow(
        "Patient Communication: Empathetic Summary",
        metadata={
            "stage": "7-message",
            "diagnosis": str(req.consensus.get("primaryDiagnosis", ""))[:120],
            "urgency": req.consensus.get("urgency", "unknown"),
            "symptoms": (req.symptoms or "")[:120],
        },
    ):
        try:
            message = await run_agent(
                message_agent,
                prompt,
                model=model_slug,
                context={"consensus": req.consensus},
            )
        except OutputGuardrailTripwireTriggered as exc:
            info = exc.guardrail_result.output.output_info
            subcode = info.get("code") if isinstance(info, dict) else None
            if subcode != "message_disclaimer_missing":
                raise
            log.info(
                "Message disclaimer missing on first attempt; retrying with corrective hint"
            )
            retried = True
            retry_reason = subcode
            message = await run_agent(
                message_agent,
                prompt + _DISCLAIMER_RETRY_HINT,
                model=model_slug,
                context={"consensus": req.consensus},
            )

    return {
        "message": message,
        "retried": retried,
        "retry_reason": retry_reason,
    }


@router.post("/api/message/followup")
async def patient_message_followup(
    req: PatientFollowUpIn,
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    """Answer patient questions after the final message; optional prior diagnostics for context."""
    model_slug = resolve_for_request(req, user, response)
    prior = ""
    if req.prior_diagnostics.strip():
        prior = f"\n\nPrior diagnostics / records the patient cites:\n{req.prior_diagnostics.strip()}"

    prompt = (
        f"Patient symptoms (original): {req.symptoms}\n\n"
        f"Intake follow-up answers: {req.followup_answers}\n\n"
        f"Structured consensus (JSON):\n{json.dumps(req.consensus, ensure_ascii=False)}\n\n"
        f"Treatment plan:\n{req.plan}\n\n"
        f"Patient-facing message already sent:\n{req.patient_message}{prior}\n\n"
        f"---\nPatient's new question:\n{req.question}"
    )
    with traced_workflow(
        "Patient Follow-up Q&A",
        metadata={
            "stage": "7b-followup-qa",
            "question": (req.question or "")[:120],
            "has_prior_diagnostics": bool(req.prior_diagnostics.strip()),
            "symptoms": (req.symptoms or "")[:120],
        },
    ):
        reply = await run_agent(followup_qa_agent, prompt, model=model_slug)
    return {"reply": reply}
