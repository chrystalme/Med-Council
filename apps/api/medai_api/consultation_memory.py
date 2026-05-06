from __future__ import annotations

import json
from typing import Any

MAX_MEMORY_DOCUMENT_CHARS = 12000
MAX_SECTION_CHARS = 3000
MAX_JSON_SECTION_CHARS = 3000
MAX_ATTACHMENT_CHARS = 2000


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _limit(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n[truncated]"


def _append_section(parts: list[str], heading: str, body: str) -> None:
    body = _limit(body, MAX_SECTION_CHARS)
    if body:
        parts.append(f"## {heading}\n{body}")


def _json_section(value: Any) -> str:
    return _limit(json.dumps(value, ensure_ascii=False), MAX_JSON_SECTION_CHARS)


def build_consultation_memory_text(
    *,
    summary: str,
    primary_dx: str | None = None,
    icd_code: str | None = None,
    urgency: str | None = None,
    confidence: int | None = None,
    attachment_texts: list[str] | None = None,
    case_state: dict[str, Any] | None = None,
) -> str:
    """Build the full document stored in vector memory for later retrieval."""
    state = case_state or {}
    parts: list[str] = []

    diagnosis_bits = [
        f"Primary diagnosis: {primary_dx}" if primary_dx else "",
        f"ICD-10: {icd_code}" if icd_code else "",
        f"Urgency: {urgency}" if urgency else "",
        f"Confidence: {confidence}%" if confidence is not None else "",
    ]
    _append_section(parts, "Diagnosis", "\n".join(bit for bit in diagnosis_bits if bit))
    _append_section(parts, "Key Findings", summary)
    _append_section(parts, "Presenting Symptoms", _as_text(state.get("symptoms")))

    roster = state.get("councilRoster")
    if isinstance(roster, list):
        labels: list[str] = []
        for physician in roster:
            if not isinstance(physician, dict):
                continue
            name = _as_text(physician.get("name"))
            specialty = _as_text(physician.get("specialty"))
            if name or specialty:
                labels.append(" - ".join(part for part in [specialty, name] if part))
        _append_section(parts, "Council Roster", "\n".join(labels))

    deliberation_bits = [
        _as_text(state.get("deliberationCaseSummary")),
        (
            "Focus areas: "
            + ", ".join(_as_text(area) for area in state.get("deliberationFocusAreas") if _as_text(area))
            if isinstance(state.get("deliberationFocusAreas"), list)
            else ""
        ),
        f"Reason: {_as_text(state.get('deliberationReason'))}"
        if _as_text(state.get("deliberationReason"))
        else "",
    ]
    _append_section(parts, "Council Deliberation", "\n".join(bit for bit in deliberation_bits if bit))

    fq_lines = state.get("fqLines")
    fq_answers = state.get("fqAnswers")
    if isinstance(fq_lines, list):
        followup: list[str] = []
        answers = fq_answers if isinstance(fq_answers, list) else []
        for idx, question in enumerate(fq_lines):
            q = _as_text(question)
            a = _as_text(answers[idx]) if idx < len(answers) else ""
            if q or a:
                followup.append(f"Q: {q}\nA: {a}")
        _append_section(parts, "Follow-up", "\n\n".join(followup))

    physicians = state.get("physicians")
    if isinstance(physicians, list):
        assessments: list[str] = []
        for physician in physicians:
            if not isinstance(physician, dict):
                continue
            specialty = _as_text(physician.get("specialty")) or "Specialist"
            name = _as_text(physician.get("name"))
            assessment = _as_text(physician.get("assessment"))
            if assessment:
                label = f"{specialty} - {name}" if name else specialty
                assessments.append(f"{label}:\n{assessment}")
        _append_section(parts, "Specialist Assessments", "\n\n".join(assessments))

    research = state.get("research")
    if isinstance(research, list) and research:
        _append_section(parts, "Research", _json_section(research))

    consensus = state.get("consensus")
    if consensus:
        _append_section(parts, "Consensus", _json_section(consensus))

    _append_section(parts, "Plan", _as_text(state.get("plan")))
    _append_section(parts, "Patient Message", _as_text(state.get("message")))

    attachments = "\n\n".join(
        _limit(_as_text(text), MAX_ATTACHMENT_CHARS)
        for text in (attachment_texts or [])
        if _as_text(text)
    )
    _append_section(parts, "Attachments", attachments)

    return _limit("\n\n".join(parts), MAX_MEMORY_DOCUMENT_CHARS)
