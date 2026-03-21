"""
MedAI Council — Function tools attached to coordinator agents (catalog lookup, etc.).
"""

from __future__ import annotations

from agents import function_tool

from council_registry import SPECIALIST_META


@function_tool(
    name_override="get_specialist_catalog",
    description_override=(
        "Return the canonical list of specialist_id values with specialty and brief scope. "
        "Call this before composing JSON that references specialist IDs (triage or deliberation roster)."
    ),
)
def get_specialist_catalog() -> str:
    """Structured roster for valid specialist_id strings in council JSON outputs."""
    lines = [
        f'{sid}\t{meta["specialty"]}\t{meta["description"]}'
        for sid, meta in SPECIALIST_META.items()
    ]
    return "specialist_id\tspecialty\tdescription\n" + "\n".join(lines)


# Tools bundle for agents that pick specialist sets from the registry
COUNCIL_COORDINATOR_TOOLS = [get_specialist_catalog]
