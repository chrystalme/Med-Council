"""
MedAI Council — FastAPI Backend
Inference : OpenRouter  →  nvidia/nemotron-3-super-120b-a12b
Tracing   : OpenAI Agents SDK  →  platform.openai.com/traces (export key separate from OpenRouter)

Environment variables required:
    OPENROUTER_API_KEY   — for model inference (https://openrouter.ai/keys)
    OPENAI_API_KEY       — for tracing export only  (https://platform.openai.com/api-keys)

Run locally:
    uvicorn main:app --reload --port 8000

Deploy (Vercel): root `app.py` re-exports this module; UI is served from `static/index.html` via GET `/` so HTML and API share one ASGI app (avoids CDN vs function routing issues).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import sqlite3
import uuid

log = logging.getLogger("medai.api")
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager, contextmanager
from typing import Annotated, Any, Optional
from dotenv import load_dotenv
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, field_validator

from auth import AuthUser, auth_configured, current_user_maybe_required, require_pro
from escalation import (
    ResendNotConfiguredError,
    maybe_escalate_oncall,
    send_patient_email,
)
from rate_limit import enforce_rate_limit, rate_limit_enabled

from agents import (
    Agent,
    InputGuardrailTripwireTriggered,
    set_default_openai_api,
    set_default_openai_client,
    set_tracing_export_api_key,
)
from agents.models.multi_provider import MultiProvider
from agents.run import Runner
from agents.run_config import RunConfig
from agents.tracing import custom_span, trace as workflow_trace
from agents.tracing.setup import get_trace_provider

from council_schemas import (
    IntakeFollowupOut,
    PatientSymptomsIn,
    parse_intake_followup_text,
    parse_research_papers,
)

from council import (
    ALL_SPECIALIST_IDS,
    MODEL,
    SPECIALIST_AGENTS,
    SPECIALIST_META,
    consensus_agent,
    deliberation_selector_agent,
    feedback_agent,
    followup_qa_agent,
    intake_agent,
    message_agent,
    plan_agent,
    research_agent,
    triage_agent,
)
from council_registry import DEFAULT_MODEL_KEY, models_for_plan, resolve_model

load_dotenv(override=True)

# OpenRouter models like nvidia/... use an unknown MultiProvider prefix — pass full ID to the client.
_council_model_provider: MultiProvider | None = None

# ── Feedback persistence (SQLite) ────────────────────────────────────────────
# Vercel's filesystem is read-only except /tmp; locally, use the project dir.
_ON_VERCEL = bool(os.environ.get("VERCEL"))
_DB_PATH = Path("/tmp/feedback.db") if _ON_VERCEL else Path(__file__).resolve().parent / "feedback.db"
FEEDBACK_SECRET = os.environ.get("FEEDBACK_SECRET") or os.environ.get("FEEDBACK_TOKEN") or secrets.token_urlsafe(32)


def _init_feedback_db() -> None:
    """Create the feedback table if it doesn't already exist."""
    con = sqlite3.connect(str(_DB_PATH))
    con.execute(
        """CREATE TABLE IF NOT EXISTS feedback (
               id         INTEGER PRIMARY KEY AUTOINCREMENT,
               rating     TEXT    NOT NULL CHECK(rating IN ('up','down')),
               comment    TEXT    NOT NULL DEFAULT '',
               symptoms   TEXT    NOT NULL DEFAULT '',
               diagnosis  TEXT    NOT NULL DEFAULT '',
               created_at TEXT    NOT NULL
           )"""
    )
    con.commit()
    con.close()


def _init_cases_db() -> None:
    """Step 3 — persisted case drafts (SQLite, same file as feedback).

    Also initialises the patient memory schema (consultations + vector embeddings)
    and the case attachments schema (Phase 3.5). All share feedback.db.
    """
    con = sqlite3.connect(str(_DB_PATH))
    con.execute(
        """CREATE TABLE IF NOT EXISTS cases (
               id         TEXT PRIMARY KEY,
               user_id    TEXT NOT NULL DEFAULT '',
               title      TEXT NOT NULL DEFAULT '',
               state      TEXT NOT NULL DEFAULT '{}',
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL
           )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS consultations (
               id           TEXT PRIMARY KEY,
               user_id      TEXT NOT NULL,
               case_id      TEXT NOT NULL,
               summary      TEXT NOT NULL,
               primary_dx   TEXT,
               icd_code     TEXT,
               urgency      TEXT,
               confidence   INTEGER,
               created_at   TEXT NOT NULL
           )"""
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_consultations_user ON consultations(user_id)"
    )
    con.commit()

    # Vector embeddings table (used by SqliteVectorStore for patient memory).
    from vector_store import get_vector_store

    try:
        get_vector_store().ensure_schema(con)
    except NotImplementedError:
        # Vertex backend — schema lives elsewhere.
        pass

    # Attachments table (Phase 3.5).
    from attachments import get_attachment_store

    try:
        get_attachment_store().ensure_schema(con)
    except NotImplementedError:
        pass

    con.close()


def _get_db() -> sqlite3.Connection:
    con = sqlite3.connect(str(_DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def _truncate(text: str, max_len: int = 120) -> str:
    """Truncate text for trace metadata (keeps traces searchable without bloating)."""
    t = (text or "").strip()
    return t[:max_len] + "…" if len(t) > max_len else t


def _flush_sdk_traces() -> None:
    """Push queued traces immediately (BatchTraceProcessor defaults to ~5s delay)."""
    try:
        get_trace_provider().force_flush()
    except Exception:
        pass


def _coerce_trace_metadata(metadata: dict | None) -> dict[str, str]:
    """Normalise trace metadata values to strings.

    The OpenAI tracing ingestion endpoint requires every metadata value to be a
    string. Different providers/models can leak non-string values (ints, bools,
    None) through call sites. Coerce here so every call site is compatible.
    """
    if not metadata:
        return {}
    out: dict[str, str] = {}
    for k, v in metadata.items():
        if v is None:
            continue
        if isinstance(v, bool):
            out[str(k)] = "true" if v else "false"
        elif isinstance(v, (str, int, float)):
            out[str(k)] = str(v)
        else:
            try:
                out[str(k)] = json.dumps(v, default=str, ensure_ascii=False)
            except Exception:
                out[str(k)] = str(v)
    return out


@contextmanager
def traced_workflow(name: str, *, group_id: str | None = None, metadata: dict | None = None):
    """OpenAI Agents SDK workflow trace + immediate export flush for the dashboard.

    Args:
        name: Workflow name shown in the trace dashboard.
        group_id: Optional session/conversation ID to link related traces.
        metadata: Arbitrary key-value pairs attached to the trace for filtering/search.
            All values are coerced to strings (tracing ingestion requires string values).
    """
    with workflow_trace(name, group_id=group_id, metadata=_coerce_trace_metadata(metadata)):
        yield
    _flush_sdk_traces()


# ─────────────────────────────────────────────────────────────────────────────
#  Startup
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: Configure OpenRouter + OpenAI Agents SDK trace export."""
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    if not openrouter_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set.")
    if not openai_key:
        raise RuntimeError("OPENAI_API_KEY is not set (used for tracing only).")

    global _council_model_provider

    # Point the OpenAI Agents SDK default OpenAI client at OpenRouter (chat completions).
    openrouter_client = AsyncOpenAI(
        api_key=openrouter_key,
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": "https://medai-council.local",
            "X-Title": "MedAI Council",
        },
    )
    set_default_openai_client(openrouter_client, use_for_tracing=False)
    set_default_openai_api("chat_completions")
    set_tracing_export_api_key(openai_key)

    _council_model_provider = MultiProvider(
        openai_client=openrouter_client,
        unknown_prefix_mode="model_id",
    )

    _init_feedback_db()
    _init_cases_db()

    print("✓ Inference  → OpenRouter  (nvidia/nemotron-3-super-120b-a12b:free)")
    print("✓ Tracing    → platform.openai.com/traces  (OpenAI Agents SDK exporter)")
    print(f"✓ Feedback   → {_DB_PATH}  (view: /feedback/{FEEDBACK_SECRET})")
    if auth_configured():
        print("✓ Auth       → Clerk JWT verification enabled (CLERK_ISSUER set)")
    else:
        print("⚠ Auth       → DISABLED (set CLERK_ISSUER to require signed sessions)")
    if rate_limit_enabled():
        print("✓ Rate limit → ENABLED (RATE_LIMIT_ENABLED=1, per-IP sliding window)")
    if os.environ.get("RESEND_API_KEY") and os.environ.get("ONCALL_DOCTOR_EMAIL"):
        print("✓ Escalation → Resend on-call notifications enabled")

    yield


app = FastAPI(
    title="MedAI Council",
    version="3.0.0",
    lifespan=lifespan,
    redirect_slashes=False,
)

_UI_INDEX = Path(__file__).resolve().parent / "static" / "index.html"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _rate_limit_middleware(request: Request, call_next):
    if request.method == "POST" and request.url.path.startswith("/api/"):
        enforce_rate_limit(request)
    return await call_next(request)


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def run_agent_raw(agent: Agent, prompt: str, *, model: str | None = None) -> Any:
    """Run an agent and return `final_output`.

    If `model` is provided (an OpenRouter model slug already resolved via
    `resolve_model`), it overrides the agent's bound model for this run. The
    MultiProvider with `unknown_prefix_mode="model_id"` accepts arbitrary
    slugs, so per-call overrides work without re-instantiating the agent.
    """
    rc = RunConfig(
        model_provider=_council_model_provider or MultiProvider(),
        model=model,  # None falls through to agent.model
        trace_include_sensitive_data=True,
    )
    result = await Runner.run(agent, prompt, run_config=rc)
    return result.final_output


async def run_agent(agent: Agent, prompt: str, *, model: str | None = None) -> str:
    """Run an agent and return its final output as a string."""
    output = await run_agent_raw(agent, prompt, model=model)
    if isinstance(output, str):
        return output
    return output.model_dump_json() if hasattr(output, "model_dump_json") else str(output)


def _resolve_for_request(
    req: BaseModel,
    user: "AuthUser | None",
    response: "Response",
) -> str:
    """Resolve the OpenRouter slug for this request, set downgrade headers, return the slug.

    Every pipeline endpoint calls this to look up the user's choice of model
    from the allowlist, enforce the free/pro tier split silently, and pass the
    slug into run_agent(...). `req.model` is an allowlist key (e.g.
    "claude-opus-4-7"); the returned value is the OpenRouter slug.
    """
    from auth import effective_plan  # local import to avoid circular at module load

    requested = getattr(req, "model", None)
    plan = effective_plan(user)
    slug, downgraded = resolve_model(requested, plan)
    if downgraded:
        response.headers["X-Model-Downgraded"] = "1"
        response.headers["X-Model-Downgrade-Reason"] = "plan_required"
    return slug


def _format_intake_questions_for_api(out: Any) -> str:
    """Parse model output into IntakeFollowupOut, then numbered lines for the UI."""
    if isinstance(out, IntakeFollowupOut):
        model = out
    else:
        model = parse_intake_followup_text(out if isinstance(out, str) else str(out))
    return "\n".join(f"{i + 1}. {q.strip()}" for i, q in enumerate(model.questions))


def parse_json(raw: str) -> dict | list:
    """
    Robustly extract JSON from model output.
    Handles: markdown fences, leading prose, trailing text.
    """
    # Strip ```json ... ``` fences
    clean = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()

    # Direct parse
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    # Find first {...} or [...]
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", clean)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"No valid JSON found in model output. Raw (first 400 chars):\n{raw[:400]}")


def _pubmed_search_papers(term: str, *, retmax: int = 4) -> list[dict]:
    """
    Model-agnostic safety net: query PubMed directly and return paper cards.

    Uses NCBI E-utilities (esearch + esummary). If anything fails, returns [].
    """
    t = (term or "").strip()
    if not t:
        return []

    try:
        # PubMed search can be brittle with long, highly specific terms. Try a few progressively
        # simpler queries to maximize hit-rate, regardless of model output format.
        words = re.findall(r"[a-zA-Z]{3,}", t.lower())
        simplified = " ".join(words[:10]) if words else t

        # A high-recall query shape for typical clinical text.
        pain_terms = ["chest pain", "angina", "chest tightness", "chest pressure"]
        ex_terms = ["exertion", "exercise", "exertional"]
        high_recall = f"({' OR '.join(pain_terms)}) AND ({' OR '.join(ex_terms)})"

        candidates = [
            t[:8000],
            simplified[:400],
            high_recall,
            (high_recall + " review").strip(),
            "chest pain review",
        ]

        ids: list[str] = []
        for cand in candidates:
            if not cand.strip():
                continue
            q = quote_plus(cand)
            esearch = (
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
                f"?db=pubmed&retmode=json&retmax={int(retmax)}&sort=relevance&term={q}"
            )
            req = Request(esearch, headers={"User-Agent": "MedAI-Council/1.0 (demo)"})
            with urlopen(req, timeout=6) as r:
                payload = json.loads(r.read().decode("utf-8", errors="replace"))
            got = payload.get("esearchresult", {}).get("idlist", []) or []
            got = [str(x) for x in got if str(x).isdigit()]
            if got:
                ids = got
                break
        if not ids:
            return []

        id_csv = ",".join(ids)
        esummary = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
            f"?db=pubmed&retmode=json&id={id_csv}"
        )
        req2 = Request(esummary, headers={"User-Agent": "MedAI-Council/1.0 (demo)"})
        with urlopen(req2, timeout=6) as r:
            summ = json.loads(r.read().decode("utf-8", errors="replace"))

        result = summ.get("result", {}) if isinstance(summ, dict) else {}
        out: list[dict] = []
        for pid in ids:
            item = result.get(pid, {})
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "") or "").strip() or f"PubMed citation (PMID {pid})"
            source = str(item.get("source", "") or "").strip() or "—"
            pubdate = str(item.get("pubdate", "") or "").strip()
            year = pubdate[:4] if pubdate[:4].isdigit() else "—"
            authors = item.get("authors", [])
            if isinstance(authors, list) and authors:
                names = [a.get("name") for a in authors if isinstance(a, dict) and a.get("name")]
                authors_s = (", ".join(names[:3]) + (" et al." if len(names) > 3 else "")) if names else "—"
            else:
                authors_s = "—"
            out.append(
                {
                    "title": title,
                    "authors": authors_s,
                    "journal": source,
                    "year": year,
                    "relevance": "PubMed search result (model-agnostic fallback).",
                    "summary": "Open the PubMed link for abstract and applicability to this specific case.",
                    "pmid": pid,
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
                }
            )
        return out[:retmax]
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
#  Request models
# ─────────────────────────────────────────────────────────────────────────────

SymptomsIn = PatientSymptomsIn


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


# ─────────────────────────────────────────────────────────────────────────────
#  Utility endpoints
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/")
async def serve_ui():
    """Single-page UI (Vercel: same function as API — avoids static `public/` taking precedence)."""
    if _UI_INDEX.is_file():
        return FileResponse(_UI_INDEX, media_type="text/html; charset=utf-8")
    raise HTTPException(status_code=404, detail="UI not found (missing static/index.html)")


@app.get("/index.html", include_in_schema=False)
async def serve_ui_index_alias():
    return RedirectResponse("/", status_code=307)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "MedAI Council",
        "version": "3.0.0",
        "model": MODEL,
        "inference": "openrouter",
    }


@app.get("/specialists")
def list_specialists():
    return {
        "specialists": [{"id": sid, **meta} for sid, meta in SPECIALIST_META.items()]
    }


@app.get("/agents")
def list_agents():
    """List all available physicians/agents for the council"""
    return {
        "physicians": [
            {
                "id": sid,
                "name": meta["name"],
                "specialty": meta["specialty"],
                "initials": meta["initials"],
            }
            for sid, meta in SPECIALIST_META.items()
        ]
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Feedback
# ─────────────────────────────────────────────────────────────────────────────

class FeedbackIn(BaseModel):
    rating: str = Field(pattern=r"^(up|down)$")
    comment: str = Field(default="", max_length=2000)
    symptoms: str = Field(default="")
    diagnosis: str = Field(default="")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cases_user_id(user: Optional[AuthUser]) -> str:
    return user.user_id if user else ""


class CaseCreateIn(BaseModel):
    title: str = Field(default="", max_length=500)


class CasePatchIn(BaseModel):
    state: dict[str, Any]
    title: str | None = Field(default=None, max_length=500)


# ─────────────────────────────────────────────────────────────────────────────
#  Case persistence (Step 3)
# ─────────────────────────────────────────────────────────────────────────────


@app.post("/api/cases")
async def cases_create(req: CaseCreateIn, user: Optional[AuthUser] = Depends(current_user_maybe_required)):
    cid = str(uuid.uuid4())
    uid = _cases_user_id(user)
    now = _utc_now()
    con = _get_db()
    con.execute(
        "INSERT INTO cases (id, user_id, title, state, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (cid, uid, req.title or "Untitled case", "{}", now, now),
    )
    con.commit()
    con.close()
    return {"id": cid, "title": req.title or "Untitled case", "created_at": now}


@app.get("/api/cases")
def cases_list(user: Optional[AuthUser] = Depends(current_user_maybe_required)):
    uid = _cases_user_id(user)
    con = _get_db()
    rows = con.execute(
        "SELECT id, title, updated_at FROM cases WHERE user_id = ? ORDER BY updated_at DESC LIMIT 50",
        (uid,),
    ).fetchall()
    con.close()
    return {"cases": [{"id": r["id"], "title": r["title"], "updated_at": r["updated_at"]} for r in rows]}


@app.get("/api/cases/{case_id}")
def cases_get(case_id: str, user: Optional[AuthUser] = Depends(current_user_maybe_required)):
    uid = _cases_user_id(user)
    con = _get_db()
    row = con.execute(
        "SELECT id, user_id, title, state, created_at, updated_at FROM cases WHERE id = ?",
        (case_id,),
    ).fetchone()
    con.close()
    if not row or row["user_id"] != uid:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        state = json.loads(row["state"] or "{}")
    except json.JSONDecodeError:
        state = {}
    return {
        "id": row["id"],
        "title": row["title"],
        "state": state,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@app.patch("/api/cases/{case_id}")
async def cases_patch(
    case_id: str,
    req: CasePatchIn,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    uid = _cases_user_id(user)
    con = _get_db()
    row = con.execute("SELECT user_id FROM cases WHERE id = ?", (case_id,)).fetchone()
    if not row or row["user_id"] != uid:
        con.close()
        raise HTTPException(status_code=404, detail="Case not found")
    now = _utc_now()
    state_json = json.dumps(req.state, ensure_ascii=False)
    if req.title is not None:
        con.execute(
            "UPDATE cases SET state = ?, title = ?, updated_at = ? WHERE id = ?",
            (state_json, req.title[:500], now, case_id),
        )
    else:
        con.execute(
            "UPDATE cases SET state = ?, updated_at = ? WHERE id = ?",
            (state_json, now, case_id),
        )
    con.commit()
    con.close()
    return {"id": case_id, "updated_at": now}


@app.post("/api/feedback", dependencies=[Depends(current_user_maybe_required)])
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


@app.get("/feedback/{token}")
def view_feedback(token: str):
    if not secrets.compare_digest(token, FEEDBACK_SECRET):
        raise HTTPException(status_code=404, detail="Not found")
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
        comment = r["comment"] or "\u2014"
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


# ─────────────────────────────────────────────────────────────────────────────
#  Model selector catalog
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/models")
async def list_models(
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    """Return the model allowlist visible to this user, with `locked` flags for
    Pro-only entries when the caller is on the Free tier. The frontend dropdown
    uses this to render the picker.
    """
    from auth import effective_plan

    plan = effective_plan(user)
    return {
        "default": DEFAULT_MODEL_KEY,
        "plan": plan,
        "models": models_for_plan(plan),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Stage 1 — Intake
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/intake/followup")
async def intake_followup(
    req: SymptomsIn,
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    model_slug = _resolve_for_request(req, user, response)
    try:
        with traced_workflow(
            "Intake Follow-up Questions",
            metadata={"stage": "1-intake", "symptoms": _truncate(req.symptoms)},
        ):
            raw_text = await run_agent(
                intake_agent,
                f"Patient self-reports: {req.symptoms}",
                model=model_slug,
            )
    except InputGuardrailTripwireTriggered as e:
        info = e.guardrail_result.output.output_info if e.guardrail_result.output else {}
        raise HTTPException(
            status_code=422,
            detail={
                "error": "non_medical_input",
                "message": (
                    "This service is designed for medical questions only. "
                    "Please describe a health concern, symptom, or medical situation."
                ),
                "reasoning": info.get("reasoning", ""),
            },
        ) from e
    try:
        return {"questions": _format_intake_questions_for_api(raw_text)}
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


# ─────────────────────────────────────────────────────────────────────────────
#  Stage 2 — Triage
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/triage")
async def triage(
    req: TriageIn,
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    model_slug = _resolve_for_request(req, user, response)
    prompt = (
        f"Patient symptoms: {req.symptoms}\n\n"
        f"Patient follow-up responses: {req.followup_answers}"
    )
    with traced_workflow(
        "Triage: Specialist Selection",
        metadata={"stage": "2-triage", "symptoms": _truncate(req.symptoms)},
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


# ─────────────────────────────────────────────────────────────────────────────
#  Stage 2b — Deliberation Expert Selection (optional alternative to triage)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/deliberation/select-experts")
async def select_deliberation_experts(
    req: TriageIn,
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    """Select 4–6 expert specialists for structured deliberation (symptoms + follow-up answers)."""
    model_slug = _resolve_for_request(req, user, response)
    prompt = (
        f"Patient symptoms: {req.symptoms}\n\n"
        f"Patient follow-up responses: {req.followup_answers}"
    )
    with traced_workflow(
        "Expert Selection for Deliberation",
        metadata={"stage": "2b-deliberation-select", "symptoms": _truncate(req.symptoms)},
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
    print("\n" + "="*80)
    print("[DELIBERATION EXPERT SELECTION]")
    print("="*80)
    print(f"Patient Symptoms: {req.symptoms}")
    print(f"\nReason for Selection:\n{data.get('reason_for_selection', 'No rationale provided')}")
    print(f"\nCase Summary: {data.get('case_summary', 'N/A')}")
    print(f"\nSelected Experts ({len(valid_ids)} total):")
    for i, expert in enumerate(experts, 1):
        print(f"  {i}. {expert['name']} ({expert['specialty']})")
    print(f"\nFocus Areas: {', '.join(data.get('focus_areas', []))}")
    print("="*80 + "\n")

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


# ─────────────────────────────────────────────────────────────────────────────
#  Stage 3 — Physician Council (one call per specialist)
# ─────────────────────────────────────────────────────────────────────────────

def _council_context_block(council_context: str) -> str:
    t = (council_context or "").strip()
    if not t:
        return ""
    return f"\n\nDeliberation lead framing (use alongside the chart):\n{t}"


@app.post("/api/council/specialist")
async def council_specialist(
    req: SpecialistIn,
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    model_slug = _resolve_for_request(req, user, response)
    if req.specialist_id not in SPECIALIST_AGENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown specialist_id '{req.specialist_id}'. Valid: {ALL_SPECIALIST_IDS}",
        )

    prior_block = ""
    if req.prior_assessments:
        prior_block = "\n\nColleague assessments (read carefully before responding):\n" + "\n\n".join(
            f"--- {a['name']} ({a['specialty']}) ---\n{a['assessment']}"
            for a in req.prior_assessments
        )

    ctx = _council_context_block(req.council_context)
    memory = _retrieve_patient_context(_cases_user_id(user), req.symptoms)
    prompt = (
        f"Patient symptoms: {req.symptoms}\n\n"
        f"Patient follow-up responses: {req.followup_answers}"
        f"{ctx}"
        f"{prior_block}"
        + (f"\n\n{memory}" if memory else "")
    )

    specialist_name = SPECIALIST_META[req.specialist_id]["name"]
    with traced_workflow(
        f"Specialist Assessment: {specialist_name}",
        metadata={
            "stage": "3-council",
            "specialist_id": req.specialist_id,
            "specialist_name": specialist_name,
            "prior_assessment_count": len(req.prior_assessments),
            "symptoms": _truncate(req.symptoms),
        },
    ):
        assessment = await run_agent(SPECIALIST_AGENTS[req.specialist_id], prompt, model=model_slug)
    return {
        "specialist": {"id": req.specialist_id, **SPECIALIST_META[req.specialist_id]},
        "assessment": assessment,
    }


@app.post("/api/council/physician")
async def council_physician(
    req: PhysicianIn,
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    """Alias for council_specialist to match frontend naming (physician_id instead of specialist_id)"""
    model_slug = _resolve_for_request(req, user, response)
    if req.physician_id not in SPECIALIST_AGENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown physician_id '{req.physician_id}'. Valid: {ALL_SPECIALIST_IDS}",
        )

    prior_block = ""
    if req.prior_assessments:
        prior_block = "\n\nColleague assessments (read carefully before responding):\n" + "\n\n".join(
            f"--- {a['name']} ({a['specialty']}) ---\n{a['assessment']}"
            for a in req.prior_assessments
        )

    ctx = _council_context_block(req.council_context)
    memory = _retrieve_patient_context(_cases_user_id(user), req.symptoms)
    attachments_block = _attachment_block_for_case(req.case_id) if req.case_id else ""
    prompt = (
        f"Patient symptoms: {req.symptoms}\n\n"
        f"Patient follow-up responses: {req.followup_answers}"
        f"{ctx}"
        f"{prior_block}"
        + (f"\n\n{attachments_block}" if attachments_block else "")
        + (f"\n\n{memory}" if memory else "")
    )

    specialist_name = SPECIALIST_META[req.physician_id]["name"]
    with traced_workflow(
        f"Specialist Assessment: {specialist_name}",
        metadata={
            "stage": "3-council",
            "specialist_id": req.physician_id,
            "specialist_name": specialist_name,
            "prior_assessment_count": len(req.prior_assessments),
            "symptoms": _truncate(req.symptoms),
        },
    ):
        assessment = await run_agent(SPECIALIST_AGENTS[req.physician_id], prompt, model=model_slug)
    return {
        "specialist": {"id": req.physician_id, **SPECIALIST_META[req.physician_id]},
        "assessment": assessment,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Stage 4 — Research
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/research")
async def research(
    req: ResearchIn,
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    model_slug = _resolve_for_request(req, user, response)
    assessments_text = "\n\n".join(
        f"{a['name']} ({a['specialty']}):\n{a['assessment']}" for a in req.assessments
    )
    prompt = (
        f"Patient symptoms: {req.symptoms}\n\n"
        f"Follow-up responses: {req.followup_answers}\n\n"
        f"Team assessments:\n{assessments_text}"
    )
    with traced_workflow(
        "Research: Evidence-Based Paper Selection",
        metadata={
            "stage": "4-research",
            "assessment_count": len(req.assessments),
            "symptoms": _truncate(req.symptoms),
        },
    ):
        raw = await run_agent(research_agent, prompt, model=model_slug)

    with custom_span("parse_research_papers", data={"source": "model_output"}):
        papers, parse_warning = parse_research_papers(raw)

    # Failsafe: if the model didn't return a usable papers array (or produced narrative-only output),
    # fetch real PubMed links based on the case text so the UI always has actionable references.
    has_any_links = any(bool((p or {}).get("url")) for p in (papers or []))
    if not has_any_links:
        with custom_span("pubmed_fallback_search", data={"reason": "no_urls_in_model_output"}):
            pubmed_term = f"{req.symptoms}\n{req.followup_answers}\n{assessments_text}"
            pubmed_papers = _pubmed_search_papers(pubmed_term, retmax=4)
        if pubmed_papers:
            papers = pubmed_papers
            parse_warning = (
                (parse_warning + " " if parse_warning else "")
                + "Recovered PubMed links via direct search fallback."
            )

    return {"papers": papers, "parse_warning": parse_warning}


# ─────────────────────────────────────────────────────────────────────────────
#  Stage 5 — Consensus / Diagnosis
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/consensus")
async def consensus(
    req: ConsensusIn,
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    model_slug = _resolve_for_request(req, user, response)
    assessments_text = "\n\n".join(
        f"{a['name']} ({a['specialty']}):\n{a['assessment']}" for a in req.assessments
    )
    research_text = "\n".join(
        f"• {r.get('title','')} ({r.get('year','')}): {r.get('summary','')}"
        for r in req.research
    )
    memory = _retrieve_patient_context(_cases_user_id(user), req.symptoms)
    attachments_block = _attachment_block_for_case(req.case_id) if req.case_id else ""
    prompt = (
        f"Patient symptoms: {req.symptoms}\n\n"
        f"Follow-up responses: {req.followup_answers}\n\n"
        f"Specialist assessments:\n{assessments_text}\n\n"
        f"Supporting research:\n{research_text}"
        + (f"\n\n{attachments_block}" if attachments_block else "")
        + (f"\n\n{memory}" if memory else "")
    )
    with traced_workflow(
        "Consensus: Integrating Multidisciplinary Assessment",
        metadata={
            "stage": "5-consensus",
            "assessment_count": len(req.assessments),
            "research_paper_count": len(req.research),
            "symptoms": _truncate(req.symptoms),
        },
    ):
        raw = await run_agent(consensus_agent, prompt, model=model_slug)

    try:
        data = parse_json(raw)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    if isinstance(data, dict):
        asyncio.create_task(
            asyncio.to_thread(
                maybe_escalate_oncall,
                consensus=data,
                symptoms=req.symptoms,
            )
        )

    return {"consensus": data}


# ─────────────────────────────────────────────────────────────────────────────
#  Stage 6 — Treatment Plan
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/plan")
async def plan(
    req: PlanIn,
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    model_slug = _resolve_for_request(req, user, response)
    assessments_text = "\n\n".join(
        f"{a['name']} ({a['specialty']}):\n{a['assessment']}" for a in req.assessments
    )
    memory = _retrieve_patient_context(_cases_user_id(user), req.symptoms)
    attachments_block = _attachment_block_for_case(req.case_id) if req.case_id else ""
    prompt = (
        f"Diagnosis: {json.dumps(req.consensus)}\n\n"
        f"Patient symptoms: {req.symptoms}\n\n"
        f"Follow-up responses: {req.followup_answers}\n\n"
        f"Specialist findings:\n{assessments_text}"
        + (f"\n\n{attachments_block}" if attachments_block else "")
        + (f"\n\n{memory}" if memory else "")
    )
    with traced_workflow(
        "Treatment Plan: Multi-Specialty Coordination",
        metadata={
            "stage": "6-plan",
            "assessment_count": len(req.assessments),
            "symptoms": _truncate(req.symptoms),
        },
    ):
        plan_text = await run_agent(plan_agent, prompt, model=model_slug)
    return {"plan": plan_text}


# ─────────────────────────────────────────────────────────────────────────────
#  Stage 7 — Patient Message
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/message")
async def patient_message(
    req: MessageIn,
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    model_slug = _resolve_for_request(req, user, response)
    prompt = (
        f"Primary diagnosis: {req.consensus.get('primaryDiagnosis')} "
        f"(confidence {req.consensus.get('confidence')}%, {req.consensus.get('urgency')} urgency)\n"
        f"ICD code: {req.consensus.get('icdCode', '')}\n"
        f"Prognosis: {req.consensus.get('prognosis')}\n"
        f"Key findings: {req.consensus.get('keyFindings')}\n\n"
        f"Treatment plan:\n{req.plan}\n\n"
        f"Original patient symptoms: {req.symptoms}"
    )
    with traced_workflow(
        "Patient Communication: Empathetic Summary",
        metadata={
            "stage": "7-message",
            "diagnosis": _truncate(str(req.consensus.get("primaryDiagnosis", ""))),
            "urgency": req.consensus.get("urgency", "unknown"),
            "symptoms": _truncate(req.symptoms),
        },
    ):
        message = await run_agent(message_agent, prompt, model=model_slug)
    return {"message": message}


@app.post("/api/message/followup")
async def patient_message_followup(
    req: PatientFollowUpIn,
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    """Answer patient questions after the final message; optional prior diagnostics for context."""
    model_slug = _resolve_for_request(req, user, response)
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
            "question": _truncate(req.question),
            "has_prior_diagnostics": bool(req.prior_diagnostics.strip()),
            "symptoms": _truncate(req.symptoms),
        },
    ):
        reply = await run_agent(followup_qa_agent, prompt, model=model_slug)
    return {"reply": reply}


# ─────────────────────────────────────────────────────────────────────────────
#  Patient memory (Phase 3) — consultations table + vector retrieval
# ─────────────────────────────────────────────────────────────────────────────

FREE_CONSULTATION_CAP = 4


def _consultation_count(con: sqlite3.Connection, user_id: str) -> int:
    row = con.execute(
        "SELECT COUNT(*) FROM consultations WHERE user_id = ?", (user_id,)
    ).fetchone()
    return int(row[0]) if row else 0


def _assert_consultation_cap(con: sqlite3.Connection, user_id: str, user_plan: str) -> None:
    if user_plan == "pro":
        return
    count = _consultation_count(con, user_id)
    if count >= FREE_CONSULTATION_CAP:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "consultation_cap",
                "cap": FREE_CONSULTATION_CAP,
                "current": count,
                "message": (
                    f"Free tier is limited to {FREE_CONSULTATION_CAP} saved consultations. "
                    "Delete one or upgrade to Pro."
                ),
            },
        )


class ConsultationSaveIn(BaseModel):
    case_id: str
    summary: Annotated[str, Field(min_length=1, max_length=8000)]
    primary_dx: str | None = None
    icd_code: str | None = None
    urgency: str | None = None
    confidence: int | None = None
    attachment_texts: list[str] = Field(default_factory=list)


@app.post("/api/patient/consultations")
async def save_consultation(
    req: ConsultationSaveIn,
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    """Persist a finished consultation and index it for vector retrieval.

    Called automatically by the frontend after the consensus completes. Both
    tiers store; Free is capped at FREE_CONSULTATION_CAP.
    """
    from auth import effective_plan
    from embeddings import get_embedding_provider
    from vector_store import get_vector_store

    user_id = _cases_user_id(user)
    plan = effective_plan(user)

    con = _get_db()
    try:
        _assert_consultation_cap(con, user_id, plan)

        consultation_id = f"con_{uuid.uuid4().hex[:24]}"
        now = datetime.now(timezone.utc).isoformat()

        con.execute(
            """
            INSERT INTO consultations
              (id, user_id, case_id, summary, primary_dx, icd_code, urgency, confidence, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                consultation_id,
                user_id,
                req.case_id,
                req.summary,
                req.primary_dx,
                req.icd_code,
                req.urgency,
                req.confidence,
                now,
            ),
        )
        con.commit()

        # Build embedding input from summary + diagnosis + attachment text.
        embed_text = "\n\n".join(
            chunk for chunk in [
                req.summary,
                f"Primary diagnosis: {req.primary_dx}" if req.primary_dx else "",
                "\n".join(t for t in req.attachment_texts if t),
            ]
            if chunk
        )

        try:
            vec = get_embedding_provider().embed(embed_text)
            get_vector_store().upsert(
                con,
                id=consultation_id,
                embedding=vec,
                metadata={
                    "user_id": user_id,
                    "case_id": req.case_id,
                    "created_at": now,
                    "primary_dx": req.primary_dx or "",
                    "urgency": req.urgency or "",
                    "confidence": req.confidence or 0,
                },
                document=req.summary[:4000],
            )
        except Exception as exc:
            log.warning("embedding/vector upsert failed; consultation saved without retrieval: %s", exc)

        remaining = None if plan == "pro" else max(0, FREE_CONSULTATION_CAP - _consultation_count(con, user_id))
        if remaining is not None:
            response.headers["X-Consultation-Remaining"] = str(remaining)

        return {
            "id": consultation_id,
            "case_id": req.case_id,
            "created_at": now,
            "remaining": remaining,
        }
    finally:
        con.close()


@app.get("/api/patient/consultations")
async def list_consultations(
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    from auth import effective_plan

    user_id = _cases_user_id(user)
    plan = effective_plan(user)

    con = _get_db()
    try:
        rows = con.execute(
            """
            SELECT id, case_id, summary, primary_dx, icd_code, urgency, confidence, created_at
            FROM consultations
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()

        if plan != "pro":
            response.headers["X-Consultation-Remaining"] = str(
                max(0, FREE_CONSULTATION_CAP - len(rows))
            )

        return {
            "plan": plan,
            "cap": None if plan == "pro" else FREE_CONSULTATION_CAP,
            "consultations": [
                {
                    "id": r["id"],
                    "case_id": r["case_id"],
                    "summary": r["summary"],
                    "primary_dx": r["primary_dx"],
                    "icd_code": r["icd_code"],
                    "urgency": r["urgency"],
                    "confidence": r["confidence"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
        }
    finally:
        con.close()


class RetrieveIn(BaseModel):
    query: Annotated[str, Field(min_length=1, max_length=8000)]
    top_k: int = 3


@app.post("/api/patient/retrieve")
async def retrieve_consultations(
    req: RetrieveIn,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    from embeddings import get_embedding_provider
    from vector_store import get_vector_store

    user_id = _cases_user_id(user)
    if not user_id:
        return {"hits": []}

    con = _get_db()
    try:
        try:
            vec = get_embedding_provider().embed(req.query)
            hits = get_vector_store().query(
                con,
                embedding=vec,
                top_k=max(1, min(10, int(req.top_k))),
                where={"user_id": user_id},
            )
        except Exception as exc:
            log.warning("retrieval failed: %s", exc)
            return {"hits": []}

        return {
            "hits": [
                {
                    "id": h.id,
                    "score": round(h.score, 4),
                    "metadata": h.metadata,
                    "document": h.document,
                }
                for h in hits
            ]
        }
    finally:
        con.close()


@app.delete("/api/patient/consultations/{consultation_id}")
async def delete_consultation(
    consultation_id: str,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    from vector_store import get_vector_store

    user_id = _cases_user_id(user)
    con = _get_db()
    try:
        row = con.execute(
            "SELECT user_id FROM consultations WHERE id = ?", (consultation_id,)
        ).fetchone()
        if row is None or (row["user_id"] or "") != user_id:
            raise HTTPException(status_code=404, detail="Consultation not found.")
        con.execute("DELETE FROM consultations WHERE id = ?", (consultation_id,))
        con.commit()
        try:
            get_vector_store().delete(con, consultation_id)
        except Exception as exc:
            log.warning("vector delete failed (ignoring): %s", exc)
        return {"ok": True}
    finally:
        con.close()


def _retrieve_patient_context(user_id: str, query: str, top_k: int = 3) -> str:
    """Return a plain-text block of similar prior consultations for injection into agent prompts.

    Returns "" when the user has no prior consultations or embedding fails —
    callers can unconditionally concatenate the result.
    """
    if not user_id or not (query or "").strip():
        return ""
    from embeddings import get_embedding_provider
    from vector_store import get_vector_store

    con = _get_db()
    try:
        try:
            vec = get_embedding_provider().embed(query)
            hits = get_vector_store().query(
                con,
                embedding=vec,
                top_k=top_k,
                where={"user_id": user_id},
            )
        except Exception as exc:
            log.warning("patient context retrieval failed: %s", exc)
            return ""

        if not hits:
            return ""

        lines = ["--- Patient's prior consultations (most relevant first) ---"]
        for h in hits:
            meta = h.metadata or {}
            date = str(meta.get("created_at") or "")[:10]
            dx = meta.get("primary_dx") or "—"
            urgency = meta.get("urgency") or ""
            conf = meta.get("confidence") or 0
            score_pct = int(round(h.score * 100))
            summary = (h.document or "")[:600].replace("\n", " ")
            lines.append(
                f"[{date} · {dx} (confidence {conf}%, {urgency}) · match {score_pct}%]\n{summary}"
            )
        lines.append("---")
        return "\n\n".join(lines)
    finally:
        con.close()


# ─────────────────────────────────────────────────────────────────────────────
#  Test attachments (Phase 3.5) — PDF / text upload attached to follow-up
# ─────────────────────────────────────────────────────────────────────────────


def _attachment_block_for_case(case_id: str, question_texts: list[str] | None = None) -> str:
    """Read attachments for a case and render as a prompt-safe text block."""
    from attachments import format_attachment_block, get_attachment_store

    con = _get_db()
    try:
        rows = get_attachment_store().list_for_case(con, case_id)
    except NotImplementedError:
        return ""
    finally:
        con.close()
    return format_attachment_block(rows, question_texts)


@app.post("/api/cases/{case_id}/attachments")
async def create_attachment(
    case_id: str,
    response: Response,
    kind: str = Form("file"),
    question_index: int | None = Form(default=None),
    text: str = Form(""),
    file: UploadFile | None = File(default=None),
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    """Attach a test-result file or pasted text to a follow-up question on a case.

    Body is multipart/form-data. Either `file` OR `text` must be non-empty.
    """
    from attachments import (
        AttachmentStoreError,
        extract_text,
        get_attachment_store,
        is_mime_supported,
    )
    from auth import effective_plan

    if kind not in ("file", "pasted"):
        raise HTTPException(status_code=400, detail="kind must be 'file' or 'pasted'.")

    user_id = _cases_user_id(user)
    plan = effective_plan(user)

    # Verify the case belongs to this user.
    con = _get_db()
    try:
        row = con.execute(
            "SELECT user_id FROM cases WHERE id = ?", (case_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Case not found.")
        if (row["user_id"] or "") != user_id:
            raise HTTPException(status_code=403, detail="This case does not belong to you.")

        filename: str | None = None
        mime: str | None = None
        blob: bytes | None = None
        payload_text = text or ""

        if kind == "file":
            if file is None:
                raise HTTPException(status_code=400, detail="kind='file' requires a `file` field.")
            blob = await file.read()
            filename = file.filename or "attachment"
            mime = file.content_type or "application/octet-stream"
            if not is_mime_supported(mime):
                raise HTTPException(
                    status_code=415,
                    detail={
                        "code": "attachment_type",
                        "message": f"Unsupported file type: {mime}",
                    },
                )
            payload_text = extract_text(blob, mime, filename)
        else:
            payload_text = payload_text.strip()
            if not payload_text:
                raise HTTPException(status_code=400, detail="kind='pasted' requires non-empty `text`.")

        try:
            store = get_attachment_store()
            row_out = store.save(
                con,
                case_id=case_id,
                user_id=user_id,
                user_plan=plan,
                kind=kind,  # type: ignore[arg-type]
                filename=filename,
                mime_type=mime,
                blob=blob,
                text=payload_text,
                question_index=question_index,
            )
        except AttachmentStoreError as exc:
            raise HTTPException(
                status_code=402,
                detail={"code": exc.code, "message": exc.message, **exc.ctx},
            ) from exc

        return {
            "id": row_out.id,
            "filename": row_out.filename,
            "mime_type": row_out.mime_type,
            "kind": row_out.kind,
            "size_bytes": row_out.size_bytes,
            "text_preview": (row_out.text or "")[:400],
            "question_index": row_out.question_index,
            "created_at": row_out.created_at,
        }
    finally:
        con.close()


@app.get("/api/cases/{case_id}/attachments")
async def list_attachments(
    case_id: str,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    from attachments import get_attachment_store

    user_id = _cases_user_id(user)
    con = _get_db()
    try:
        row = con.execute("SELECT user_id FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Case not found.")
        if (row["user_id"] or "") != user_id:
            raise HTTPException(status_code=403, detail="This case does not belong to you.")

        rows = get_attachment_store().list_for_case(con, case_id)
        return {
            "attachments": [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "filename": r.filename,
                    "mime_type": r.mime_type,
                    "size_bytes": r.size_bytes,
                    "text_preview": (r.text or "")[:400],
                    "question_index": r.question_index,
                    "created_at": r.created_at,
                }
                for r in rows
            ]
        }
    finally:
        con.close()


@app.delete("/api/cases/{case_id}/attachments/{attachment_id}")
async def delete_attachment(
    case_id: str,
    attachment_id: str,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    from attachments import get_attachment_store

    user_id = _cases_user_id(user)
    con = _get_db()
    try:
        ok = get_attachment_store().delete(con, attachment_id, user_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Attachment not found.")
        return {"ok": True}
    finally:
        con.close()


# ─────────────────────────────────────────────────────────────────────────────
#  Voice I/O (Phase 2) — Whisper transcription + OpenAI TTS (Pro-only)
# ─────────────────────────────────────────────────────────────────────────────

_SPEECH_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}


@app.post("/api/speech/transcribe")
async def speech_transcribe(
    audio: UploadFile = File(...),
    user: AuthUser = Depends(require_pro),  # noqa: B008 — FastAPI dep pattern
):
    """Transcribe an uploaded audio blob via the configured SpeechProvider.

    Free tier is expected to use the browser's Web Speech API client-side;
    this endpoint gates access to the higher-quality Whisper flow and is
    behind `require_pro`.
    """
    from speech import get_speech_provider

    try:
        data = await audio.read()
        if not data:
            raise HTTPException(status_code=400, detail="Empty audio upload")
        mime = audio.content_type or "audio/webm"
        filename = audio.filename or "audio.webm"
        provider = get_speech_provider()
        text = provider.transcribe(data, mime, filename=filename)
        return {"text": text}
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("transcription failed")
        raise HTTPException(status_code=502, detail={"code": "transcribe_failed", "message": str(exc)[:200]}) from exc


class TTSIn(BaseModel):
    text: Annotated[str, Field(min_length=1, max_length=4000)]
    voice: str = "alloy"


@app.post("/api/speech/synthesize")
async def speech_synthesize(
    req: TTSIn,
    user: AuthUser = Depends(require_pro),
):
    """Synthesise an mp3 from `text` via the configured SpeechProvider.

    Free tier uses `window.speechSynthesis` on the client; this endpoint is
    Pro-only for higher-quality voices.
    """
    from speech import get_speech_provider

    voice = req.voice if req.voice in _SPEECH_VOICES else "alloy"
    try:
        provider = get_speech_provider()
        audio_bytes = provider.synthesize(req.text, voice=voice)
        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers={"Cache-Control": "no-store"},
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("synthesis failed")
        raise HTTPException(status_code=502, detail={"code": "synthesize_failed", "message": str(exc)[:200]}) from exc


# ─────────────────────────────────────────────────────────────────────────────
#  Email to patient (Pro only) — send plan + patient message via Resend
# ─────────────────────────────────────────────────────────────────────────────


class EmailToPatientIn(BaseModel):
    to: Annotated[str, Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]
    patient_name: str | None = None
    subject: str | None = None
    consensus: dict | None = None
    plan: str = ""
    message: str = ""


@app.post("/api/patient/email")
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
