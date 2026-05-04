"""Pydantic request schemas for the FastAPI agent-running endpoints.

Extracted from main.py so the route handlers and the data they accept
can evolve independently. Every agent-stage endpoint takes a subclass of
`_ModeledRequest`, which carries the optional `model` and `case_id`
keys shared by all stages.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, field_validator


class _ModeledRequest(BaseModel):
    """Mixin: every agent-running endpoint accepts an optional `model` key.

    The key must match an entry in council_registry.MODELS. Free-tier users
    asking for a Pro model are silently downgraded (see resolve_model),
    with X-Model-Downgraded: 1 set on the response.

    Also an optional `case_id` so Phase 3.5 attachments can be fetched and
    injected into the prompt without needing a separate lookup roundtrip.
    """

    model: str | None = None
    case_id: str | None = None


class TriageIn(_ModeledRequest):
    symptoms: str
    followup_answers: str


class SpecialistIn(_ModeledRequest):
    specialist_id: str
    symptoms: str
    followup_answers: str
    prior_assessments: list[dict]
    council_context: str = ""


class PhysicianIn(_ModeledRequest):
    """Alias for SpecialistIn to match frontend naming"""
    physician_id: str
    symptoms: str
    followup_answers: str
    prior_assessments: list[dict]
    council_context: str = ""


class ResearchIn(_ModeledRequest):
    symptoms: str
    followup_answers: str
    assessments: list[dict]


class ConsensusIn(_ModeledRequest):
    symptoms: str
    followup_answers: str
    assessments: list[dict]
    research: list[dict]


class PlanIn(_ModeledRequest):
    symptoms: str
    followup_answers: str
    consensus: dict
    assessments: list[dict]


class MessageIn(_ModeledRequest):
    symptoms: str
    consensus: dict
    plan: str


class PatientFollowUpIn(_ModeledRequest):
    """Post–patient-message Q&A; optional prior diagnostics for reconciling with council output."""

    question: Annotated[str, Field(min_length=1, max_length=8000)]
    prior_diagnostics: str = ""
    symptoms: Annotated[str, Field(min_length=1)]
    followup_answers: str = ""
    consensus: dict
    plan: str
    patient_message: str

    @field_validator("question", "prior_diagnostics", "symptoms", "followup_answers", "patient_message", mode="before")
    @classmethod
    def strip_text(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


__all__ = [
    "_ModeledRequest",
    "TriageIn",
    "SpecialistIn",
    "PhysicianIn",
    "ResearchIn",
    "ConsensusIn",
    "PlanIn",
    "MessageIn",
    "PatientFollowUpIn",
]
