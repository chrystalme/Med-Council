"""
MedAI Council — Handoff graph: stage router and optional specialist pool.

Handoffs are SDK `Handoff` objects so the model can delegate via tool calls.
The FastAPI app still calls individual agents per route; the router is available
for single-session orchestration or future Runner-based flows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents.handoffs import handoff

if TYPE_CHECKING:
    from agents import Agent


def build_stage_router_handoffs(
    intake: Agent,
    triage: Agent,
    deliberation_selector: Agent,
    research: Agent,
    consensus: Agent,
    plan: Agent,
    message: Agent,
) -> list:
    """Handoffs from the stage router to each pipeline agent (one tool per stage)."""
    return [
        handoff(
            intake,
            tool_name_override="transfer_to_intake_coordinator",
            tool_description_override=(
                "Hand off to the intake coordinator to generate follow-up questions "
                "from the patient's initial symptoms text."
            ),
        ),
        handoff(
            triage,
            tool_name_override="transfer_to_triage_director",
            tool_description_override=(
                "Hand off to the triage director to select 3–5 reviewing specialists "
                "from symptoms plus follow-up answers."
            ),
        ),
        handoff(
            deliberation_selector,
            tool_name_override="transfer_to_deliberation_selector",
            tool_description_override=(
                "Hand off to select 4–6 deliberation experts from symptoms and follow-up answers."
            ),
        ),
        handoff(
            research,
            tool_name_override="transfer_to_research_specialist",
            tool_description_override=(
                "Hand off to the research specialist to propose evidence papers from case text."
            ),
        ),
        handoff(
            consensus,
            tool_name_override="transfer_to_chief_of_medicine",
            tool_description_override=(
                "Hand off to synthesize multidisciplinary input into diagnostic consensus JSON."
            ),
        ),
        handoff(
            plan,
            tool_name_override="transfer_to_care_coordinator",
            tool_description_override="Hand off to produce the structured treatment plan sections.",
        ),
        handoff(
            message,
            tool_name_override="transfer_to_patient_communication",
            tool_description_override=(
                "Hand off to draft the warm patient-facing summary and validation pass."
            ),
        ),
    ]


def build_specialist_handoffs(specialist_agents: dict[str, Agent]) -> list:
    """One handoff per council specialist (for triage-style routing experiments)."""
    out = []
    for sid, agent in specialist_agents.items():
        safe = sid.replace(" ", "_")
        out.append(
            handoff(
                agent,
                tool_name_override=f"consult_specialist_{safe}",
                tool_description_override=(
                    f"Route the case to {agent.name} ({sid}) for a specialty assessment."
                ),
            )
        )
    return out
