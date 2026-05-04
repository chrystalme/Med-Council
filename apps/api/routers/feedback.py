"""Feedback routes — submission + admin viewer.

POST /api/feedback runs the feedback agent over the user's rating;
GET /feedback/{token} renders the admin HTML viewer guarded by a
constant-time secret comparison against FEEDBACK_SECRET.
"""

from __future__ import annotations

import json
import os
import secrets

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from agent_runtime import run_agent, traced_workflow
from auth import current_user_maybe_required
from council import feedback_agent

router = APIRouter()


# `FEEDBACK_SECRET` is read at module load like the original main.py — the
# same env-var fallback chain (`FEEDBACK_SECRET` → `FEEDBACK_TOKEN` → random)
# is preserved so existing deployments continue to work.
FEEDBACK_SECRET = (
    os.environ.get("FEEDBACK_SECRET")
    or os.environ.get("FEEDBACK_TOKEN")
    or secrets.token_urlsafe(32)
)


class FeedbackIn(BaseModel):
    rating: str = Field(pattern=r"^(up|down)$")
    comment: str = Field(default="", max_length=2000)
    symptoms: str = Field(default="")
    diagnosis: str = Field(default="")


@router.post("/api/feedback", dependencies=[Depends(current_user_maybe_required)])
async def submit_feedback(req: FeedbackIn):
    prompt = json.dumps({
        "rating": req.rating,
        "comment": req.comment,
        "symptoms": req.symptoms,
        "diagnosis": req.diagnosis,
    })
    with traced_workflow(
        "Patient Feedback",
        metadata={"stage": "feedback", "rating": req.rating},
    ):
        await run_agent(feedback_agent, prompt)
    return {"status": "ok"}


@router.get("/feedback/{token}")
def view_feedback(token: str):
    if not secrets.compare_digest(token, FEEDBACK_SECRET):
        raise HTTPException(status_code=404, detail="Not found")
    # Lazy-import _get_db from main to avoid a hard import cycle. main.py owns
    # the DB factory until db.py absorbs it (next refactor pass).
    from main import _get_db

    con = _get_db()
    rows = con.execute(
        "SELECT id, rating, comment, symptoms, diagnosis, created_at FROM feedback ORDER BY id DESC"
    ).fetchall()
    con.close()

    up = sum(1 for r in rows if r["rating"] == "up")
    down = sum(1 for r in rows if r["rating"] == "down")

    rows_html = ""
    for r in rows:
        emoji = "\U0001f44d" if r["rating"] == "up" else "\U0001f44e"
        comment = r["comment"] or "—"
        rows_html += (
            f'<tr><td>{r["id"]}</td><td style="font-size:22px">{emoji}</td>'
            f'<td>{_h(comment)}</td><td class="dim">{_h(r["symptoms"][:80])}</td>'
            f'<td class="dim">{_h(r["diagnosis"][:80])}</td>'
            f'<td class="dim">{r["created_at"][:19].replace("T"," ")}</td></tr>'
        )

    return HTMLResponse(_FEEDBACK_PAGE.format(
        total=len(rows), up=up, down=down, rows=rows_html,
    ))


def _h(text: str) -> str:
    """Minimal HTML-escape for feedback viewer."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


_FEEDBACK_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MedAI Feedback</title>
<style>
  body {{ background:#06101e; color:#c0d4ec; font-family:'DM Sans',system-ui,sans-serif; padding:40px 24px; }}
  h1 {{ color:#e6f0ff; font-size:24px; margin-bottom:6px; }}
  .stats {{ margin-bottom:24px; color:#4a9eff; font-size:15px; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  th {{ text-align:left; padding:10px 8px; border-bottom:1px solid rgba(255,255,255,0.1); color:#4a6280; font-weight:500; }}
  td {{ padding:10px 8px; border-bottom:1px solid rgba(255,255,255,0.05); vertical-align:top; }}
  .dim {{ color:#4a6280; font-size:13px; max-width:200px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  tr:hover td {{ background:rgba(255,255,255,0.03); }}
  .empty {{ text-align:center; padding:60px 0; color:#4a6280; }}
</style></head><body>
<h1>MedAI Council Feedback</h1>
<div class="stats">{total} responses &middot; {up} positive &middot; {down} negative</div>
<table><thead><tr><th>#</th><th>Rating</th><th>Comment</th><th>Symptoms</th><th>Diagnosis</th><th>Time</th></tr></thead>
<tbody>{rows}</tbody></table>
</body></html>"""
