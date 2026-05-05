"""
MedAI Council — Function tools attached to coordinator agents (catalog lookup, feedback, etc.).
"""

from __future__ import annotations

from datetime import datetime, timezone

from agents import function_tool

import db as _db

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


@function_tool(
    name_override="save_feedback",
    description_override=(
        "Save patient feedback to the database. Call this exactly once with the rating, "
        "optional comment, symptoms summary, and diagnosis. rating must be 'up' or 'down'."
    ),
)
def save_feedback(rating: str, comment: str, symptoms: str, diagnosis: str) -> str:
    """Persist a feedback row via the configured DB and return confirmation."""
    if rating not in ("up", "down"):
        return "ERROR: rating must be 'up' or 'down'."
    con = _db.connect()
    try:
        con.execute(
            "INSERT INTO feedback (rating, comment, symptoms, diagnosis, created_at) VALUES (%s, %s, %s, %s, %s)",
            (
                rating,
                (comment or "").strip()[:2000],
                (symptoms or "")[:500],
                (diagnosis or "")[:500],
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        con.commit()
    finally:
        con.close()
    return f"Feedback saved: rating={rating}"


# Tools bundle for agents that pick specialist sets from the registry
COUNCIL_COORDINATOR_TOOLS = [get_specialist_catalog]

# Tools bundle for the feedback agent
FEEDBACK_TOOLS = [save_feedback]
