"""Email-to-patient route (Pro-only).

POST /api/patient/email — send the coordinated plan + patient message to
the patient's inbox via Resend. Requires RESEND_API_KEY +
RESEND_FROM_EMAIL to be configured at the container.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import AuthUser, require_pro
from escalation import ResendNotConfiguredError, send_patient_email

log = logging.getLogger("medai.email")

router = APIRouter()


class EmailToPatientIn(BaseModel):
    to: Annotated[str, Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]
    patient_name: str | None = None
    subject: str | None = None
    consensus: dict | None = None
    plan: str = ""
    message: str = ""


@router.post("/api/patient/email")
async def email_patient(
    req: EmailToPatientIn,
    user: AuthUser = Depends(require_pro),
):
    """Send the coordinated plan + patient message to the patient's inbox via Resend.

    Pro-only. Requires RESEND_API_KEY + RESEND_FROM_EMAIL to be configured.
    """
    if not (req.plan.strip() or req.message.strip()):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "empty_email",
                "message": "Provide a plan, a patient message, or both before sending.",
            },
        )

    c = req.consensus or {}
    primary_dx = (
        c.get("primaryDiagnosis")
        or c.get("primary_diagnosis")
        or None
    )
    urgency = c.get("urgency") or c.get("urgencyLevel") or None
    confidence = c.get("confidence") if isinstance(c.get("confidence"), (int, float)) else None

    try:
        result = send_patient_email(
            to=req.to,
            patient_name=req.patient_name,
            subject=req.subject,
            primary_dx=primary_dx,
            urgency=urgency,
            confidence=confidence,
            plan_md=req.plan,
            message_md=req.message,
            reply_to=user.email,
        )
    except ResendNotConfiguredError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "email_not_configured",
                "message": str(exc),
            },
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "email_send_failed", "message": str(exc)[:400]},
        ) from exc

    log.info("patient email sent by user=%s to=%s", user.user_id, req.to)
    return {"ok": True, "provider_id": result.get("id")}
