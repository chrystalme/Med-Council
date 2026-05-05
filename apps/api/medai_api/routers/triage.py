"""Stage 2 — triage and deliberation expert selection.

Two routes share the same TriageIn body shape and the same downstream
JSON-parse → specialist-list normalisation pattern:

  POST /api/triage                     — pick a small set of specialists
  POST /api/deliberation/select-experts — pick a 4–6 expert deliberation panel
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response

from agent_runtime import (
    parse_json,
    resolve_for_request,
    run_agent,
    traced_workflow,
)
from auth import AuthUser, current_user_maybe_required
from council import (
    ALL_SPECIALIST_IDS,
    SPECIALIST_META,
    deliberation_selector_agent,
    triage_agent,
)
from schemas import TriageIn

router = APIRouter()


@router.post("/api/triage")
async def triage(
    req: TriageIn,
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    model_slug = resolve_for_request(req, user, response)
    prompt = (
        f"Patient symptoms: {req.symptoms}\n\n"
        f"Patient follow-up responses: {req.followup_answers}"
    )
    with traced_workflow(
        "Triage: Specialist Selection",
        metadata={"stage": "2-triage", "symptoms": (req.symptoms or "")[:120]},
    ):
        raw = await run_agent(triage_agent, prompt, model=model_slug)

    try:
        data = parse_json(raw)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Validate and normalise selected specialist IDs
    raw_ids: list[str] = data.get("selected_specialists", [])
    valid_ids = [sid for sid in raw_ids if sid in ALL_SPECIALIST_IDS]
    if "internal_medicine" not in valid_ids:
        valid_ids.insert(0, "internal_medicine")

    specialists = [{"id": sid, **SPECIALIST_META[sid]} for sid in valid_ids]

    return {
        "selected_specialist_ids": valid_ids,
        "specialists": specialists,
        "reasoning": data.get("reasoning", ""),
        "urgency_flag": data.get("urgency_flag", "routine"),
    }


@router.post("/api/deliberation/select-experts")
async def select_deliberation_experts(
    req: TriageIn,
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    """Select 4–6 expert specialists for structured deliberation (symptoms + follow-up answers)."""
    model_slug = resolve_for_request(req, user, response)
    prompt = (
        f"Patient symptoms: {req.symptoms}\n\n"
        f"Patient follow-up responses: {req.followup_answers}"
    )
    with traced_workflow(
        "Expert Selection for Deliberation",
        metadata={"stage": "2b-deliberation-select", "symptoms": (req.symptoms or "")[:120]},
    ):
        raw = await run_agent(deliberation_selector_agent, prompt, model=model_slug)

    try:
        data = parse_json(raw)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Validate expert IDs
    expert_ids: list[str] = data.get("deliberation_experts", [])
    valid_ids = [sid for sid in expert_ids if sid in ALL_SPECIALIST_IDS]

    # Ensure internal_medicine is included
    if "internal_medicine" not in valid_ids:
        valid_ids.insert(0, "internal_medicine")

    # Ensure pharmacology if medications mentioned
    case_text = f"{req.symptoms}\n{req.followup_answers}".lower()
    has_medication_keywords = any(
        keyword in case_text
        for keyword in ["medication", "drug", "medicine", "taking", "takes", "prescribed", "pill", "tablet"]
    )
    _min, _max = 4, 6
    if has_medication_keywords and "pharmacology" not in valid_ids and len(valid_ids) < _max:
        valid_ids.insert(1, "pharmacology")

    # Enforce 4–6 specialists
    if len(valid_ids) < _min:
        for sid in ALL_SPECIALIST_IDS:
            if sid not in valid_ids and len(valid_ids) < _min:
                valid_ids.append(sid)
    elif len(valid_ids) > _max:
        valid_ids = valid_ids[:_max]

    experts = [{"id": sid, **SPECIALIST_META[sid]} for sid in valid_ids]

    # Print detailed selection information
    print("\n" + "=" * 80)
    print("[DELIBERATION EXPERT SELECTION]")
    print("=" * 80)
    print(f"Patient Symptoms: {req.symptoms}")
    print(f"\nReason for Selection:\n{data.get('reason_for_selection', 'No rationale provided')}")
    print(f"\nCase Summary: {data.get('case_summary', 'N/A')}")
    print(f"\nSelected Experts ({len(valid_ids)} total):")
    for i, expert in enumerate(experts, 1):
        print(f"  {i}. {expert['name']} ({expert['specialty']})")
    print(f"\nFocus Areas: {', '.join(data.get('focus_areas', []))}")
    print("=" * 80 + "\n")

    # Format expert selection for display
    expert_display = "\n".join(
        f"• **{expert['name']}** — {expert['specialty']}"
        for expert in experts
    )

    return {
        "deliberation_experts": valid_ids,
        "experts": experts,
        "reason_for_selection": data.get("reason_for_selection", ""),
        "case_summary": data.get("case_summary", ""),
        "focus_areas": data.get("focus_areas", []),
        "display_text": f"**Selected Deliberation Experts ({len(valid_ids)} members)**\n\n{expert_display}\n\n**Reason for Selection:**\n{data.get('reason_for_selection', '')}\n\n**Case Focus:**\n{data.get('case_summary', '')}",
    }
