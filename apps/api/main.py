"""
MedAI Council — FastAPI Backend
Inference : OpenRouter  →  nvidia/nemotron-3-super-120b-a12b
Tracing   : OpenAI Agents SDK  →  platform.openai.com/traces + optional Langfuse

Environment variables required:
    OPENROUTER_API_KEY   — for model inference (https://openrouter.ai/keys)
    OPENAI_API_KEY       — for tracing export only  (https://platform.openai.com/api-keys)

Run locally:
    uvicorn main:app --reload --port 8000

Deploy target: GCP Cloud Run. Persistent state lives in Postgres (local dev:
your local Postgres; prod: Cloud SQL). Blob/file storage is abstracted in
`storage.py` — local filesystem in dev, GCS bucket when deployed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import uuid

log = logging.getLogger("medai.api")
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Annotated, Any, Optional
from dotenv import load_dotenv

# `auth` snapshots CLERK_ISSUER (and related env) at import time — load `.env` first.
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
load_dotenv(override=True)

from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
    set_default_openai_api,
    set_default_openai_client,
    set_tracing_export_api_key,
)
from agents.models.multi_provider import MultiProvider
from agents.tracing import custom_span

from council_schemas import (
    PatientSymptomsIn,
    parse_research_papers,
)
from consultation_memory import build_consultation_memory_text
from langfuse_tracing import configure_langfuse

from agent_runtime import (
    _DirectOpenAICompatibleProvider,
    _truncate,
    format_intake_questions_for_api as _format_intake_questions_for_api,
    parse_json,
    resolve_for_request as _resolve_for_request,
    run_agent,
    run_agent_raw,  # re-exported so tests/output_guardrails.py can patch main.run_agent_raw
    set_providers as _set_runtime_providers,
    traced_workflow,
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
from council_registry import DEFAULT_MODEL_KEY, models_for_plan

# ── Persistence (Postgres via db.py; schema owned by Alembic) ────────────────
import db as _db

FEEDBACK_SECRET = os.environ.get("FEEDBACK_SECRET") or os.environ.get("FEEDBACK_TOKEN") or secrets.token_urlsafe(32)


def _run_migrations() -> None:
    """Run pending Alembic migrations against `DATABASE_URL`.

    Safe for single-instance startup. For multi-instance Cloud Run deploys, run
    this as a separate Cloud Run Job before rolling the service — two instances
    starting simultaneously can race on `alembic_version`. Skipped entirely for
    the SQLite legacy fallback because Alembic targets Postgres only.
    """
    if _db.get_driver() != "postgres":
        log.warning("Skipping migrations — DATABASE_URL is not Postgres.")
        return
    if os.environ.get("SKIP_MIGRATIONS") == "1":
        log.info("SKIP_MIGRATIONS=1 — leaving schema untouched.")
        return
    from alembic import command
    from alembic.config import Config as AlembicConfig

    cfg = AlembicConfig(str(Path(__file__).resolve().parent / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent / "alembic"))
    command.upgrade(cfg, "head")


def _get_db():
    """Open a new DB connection using the driver selected by `DATABASE_URL`."""
    return _db.connect()


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


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
    langfuse_enabled = configure_langfuse()

    council_provider = MultiProvider(
        openai_client=openrouter_client,
        unknown_prefix_mode="model_id",
    )
    vertex_provider: _DirectOpenAICompatibleProvider | None = None

    # Vertex AI: serves every in-house model — the free-tier default
    # (gemini-2.5-flash-lite) and the Pro-tier Gemini / Claude / Llama entries.
    # Auth: ADC on Cloud Run (via the runtime SA with roles/aiplatform.user) or
    # `gcloud auth application-default login` locally. No static API key.
    vertex_project = (
        os.environ.get("VERTEX_PROJECT")
        or os.environ.get("GCP_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
    )
    vertex_location = os.environ.get("VERTEX_LOCATION", "us-central1")
    if vertex_project:
        try:
            import google.auth
            from google.auth.transport.requests import Request as _GRequest
            import httpx

            creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )

            class _VertexTokenAuth(httpx.Auth):
                """Refreshes the ADC token on every request; credentials are
                thread-safe and refresh lazily when `creds.valid` flips false."""

                def auth_flow(self, request):
                    if not creds.valid:
                        creds.refresh(_GRequest())
                    request.headers["Authorization"] = f"Bearer {creds.token}"
                    yield request

            vertex_http = httpx.AsyncClient(auth=_VertexTokenAuth())
            vertex_base = (
                f"https://{vertex_location}-aiplatform.googleapis.com/v1/"
                f"projects/{vertex_project}/locations/{vertex_location}/"
                f"endpoints/openapi"
            )
            vertex_client = AsyncOpenAI(
                api_key="UNUSED",  # real auth is via _VertexTokenAuth on the httpx client
                base_url=vertex_base,
                http_client=vertex_http,
            )
            vertex_provider = _DirectOpenAICompatibleProvider(
                vertex_client, default_model="google/gemini-2.5-flash-lite"
            )
        except Exception as exc:
            log.warning(
                "Vertex AI client failed to initialise (%s) — free-tier model will "
                "return provider_unavailable until the runtime SA has "
                "roles/aiplatform.user and the aiplatform.googleapis.com API is enabled.",
                exc,
            )
    else:
        log.warning(
            "VERTEX_PROJECT / GCP_PROJECT / GOOGLE_CLOUD_PROJECT is not set — "
            "Vertex AI calls will return provider_unavailable. Set it on the container."
        )

    _set_runtime_providers(council=council_provider, vertex=vertex_provider)
    _run_migrations()

    default_label = f"vertex:{vertex_provider._default_model}" if vertex_provider else "none"
    print(f"✓ Inference  → Vertex AI  ({default_label}) + OpenRouter (gpt-5 only)")
    tracing_targets = "platform.openai.com/traces + Langfuse" if langfuse_enabled else "platform.openai.com/traces"
    print(f"✓ Tracing    → {tracing_targets}")
    print(f"✓ Database   → {_db.get_driver()}  (view feedback: /feedback/{FEEDBACK_SECRET})")
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

def _allowed_origins() -> list[str]:
    """Comma-separated origin list from ALLOWED_ORIGINS. Defaults to `*` for dev.

    Production should set this to the frontend origin(s), e.g.
    ``ALLOWED_ORIGINS=https://council.example.com,https://staging.council.example.com``.
    When the web container reverse-proxies `/api/*` (Next.js rewrites), the
    browser only ever sees same-origin requests and this list is a defence-in-depth.
    """
    raw = os.environ.get("ALLOWED_ORIGINS", "*").strip()
    if raw == "*" or not raw:
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)


# ── Routers (extracted from main.py) ────────────────────────────────────────
from routers import meta as _meta_router  # noqa: E402

app.include_router(_meta_router.router)


@app.exception_handler(OutputGuardrailTripwireTriggered)
async def _output_guardrail_handler(
    request: Request, exc: OutputGuardrailTripwireTriggered,
):
    """Translate output-guardrail tripwires into HTTP 422 with structured detail.

    Mirror of the input-guardrail handler at the route level
    (see /api/intake — InputGuardrailTripwireTriggered branch).

    Top-level ``code`` is the stable value the frontend matches on
    (``output_guardrail_failed``); ``subcode`` carries the specific check that
    failed (e.g. ``consensus_icd_invalid``) for dashboards/QA without coupling
    the FE to every variant. Specificity-based dispatch ensures this fires
    before the generic Exception handler below.
    """
    from fastapi.responses import JSONResponse

    res = exc.guardrail_result
    info = res.output.output_info if isinstance(res.output.output_info, dict) else {}
    agent_name = getattr(res.agent, "name", "unknown")
    guardrail_name = res.guardrail.get_name()
    subcode = info.get("code") or "output_guardrail_failed"
    message = info.get("message") or (
        "The model produced an invalid response for this stage. "
        "Try again — and if the problem persists, switch to a different model."
    )
    log.warning(
        "OutputGuardrail tripwire: agent=%s guardrail=%s subcode=%s path=%s",
        agent_name, guardrail_name, subcode, request.url.path,
    )
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "code": "output_guardrail_failed",
                "subcode": subcode,
                "message": message,
                "stage": agent_name,
                "guardrail": guardrail_name,
                "failed_check": info.get("failed_check"),
            },
        },
    )


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    """Last-resort handler so the frontend sees *what* broke instead of a bare 500.

    Logs the full traceback server-side and returns a structured JSON body the
    web app's `CouncilApiError` parser can surface in the Fault banner. Only
    catches *unhandled* exceptions — HTTPException still follows its own path.
    """
    from fastapi.responses import JSONResponse

    log.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": "internal_error",
                "message": f"{type(exc).__name__}: {str(exc)[:300]}",
                "path": request.url.path,
            }
        },
    )


@app.middleware("http")
async def _rate_limit_middleware(request: Request, call_next):
    if request.method == "POST" and request.url.path.startswith("/api/"):
        enforce_rate_limit(request)
    return await call_next(request)


# Runtime helpers (run_agent, run_agent_raw, traced_workflow, parse_json, …)
# now live in agent_runtime.py and are imported at the top of this file.


from external.pubmed import search_papers as _pubmed_search_papers


# ─────────────────────────────────────────────────────────────────────────────
#  Request models
# ─────────────────────────────────────────────────────────────────────────────

SymptomsIn = PatientSymptomsIn


from schemas import (
    ConsensusIn,
    MessageIn,
    PatientFollowUpIn,
    PhysicianIn,
    PlanIn,
    ResearchIn,
    SpecialistIn,
    TriageIn,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Utility endpoints
# ─────────────────────────────────────────────────────────────────────────────


# Meta routes (/, /health, /specialists, /agents, /api/me, /api/models) live
# in routers/meta.py and are registered below the FastAPI() construction.


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
        "INSERT INTO cases (id, user_id, title, state, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s)",
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
        "SELECT id, title, updated_at FROM cases WHERE user_id = %s ORDER BY updated_at DESC LIMIT 50",
        (uid,),
    ).fetchall()
    con.close()
    return {"cases": [{"id": r["id"], "title": r["title"], "updated_at": r["updated_at"]} for r in rows]}


@app.get("/api/cases/{case_id}")
def cases_get(case_id: str, user: Optional[AuthUser] = Depends(current_user_maybe_required)):
    uid = _cases_user_id(user)
    con = _get_db()
    row = con.execute(
        "SELECT id, user_id, title, state, created_at, updated_at FROM cases WHERE id = %s",
        (case_id,),
    ).fetchone()
    con.close()
    if not row or row["user_id"] != uid:
        raise HTTPException(status_code=404, detail="Case not found")
    # JSONB returns a dict via psycopg; string fallback handles legacy sqlite rows.
    raw_state = row["state"]
    if isinstance(raw_state, str):
        try:
            state = json.loads(raw_state or "{}")
        except json.JSONDecodeError:
            state = {}
    else:
        state = raw_state or {}
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
    row = con.execute("SELECT user_id FROM cases WHERE id = %s", (case_id,)).fetchone()
    if not row or row["user_id"] != uid:
        con.close()
        raise HTTPException(status_code=404, detail="Case not found")
    now = _utc_now()
    state_json = json.dumps(req.state, ensure_ascii=False)
    if req.title is not None:
        con.execute(
            "UPDATE cases SET state = %s, title = %s, updated_at = %s WHERE id = %s",
            (state_json, req.title[:500], now, case_id),
        )
    else:
        con.execute(
            "UPDATE cases SET state = %s, updated_at = %s WHERE id = %s",
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

# /api/me and /api/models are registered via routers/meta.py.


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
    attachments_block = _attachment_block_for_case(req.case_id, _cases_user_id(user)) if req.case_id else ""
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
        try:
            with custom_span("pubmed_fallback_search", data={"reason": "no_urls_in_model_output"}):
                pubmed_term = f"{req.symptoms}\n{req.followup_answers}\n{assessments_text}"
                pubmed_papers = _pubmed_search_papers(pubmed_term, retmax=4)
            if pubmed_papers:
                papers = pubmed_papers
                parse_warning = (
                    (parse_warning + " " if parse_warning else "")
                    + "Recovered PubMed links via direct search fallback."
                )
        except Exception as exc:
            # NCBI rate-limits + network timeouts shouldn't fail the whole research stage.
            log.warning("pubmed fallback search failed: %s", exc)
            parse_warning = (
                (parse_warning + " " if parse_warning else "")
                + "PubMed fallback unavailable."
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
    attachments_block = _attachment_block_for_case(req.case_id, _cases_user_id(user)) if req.case_id else ""
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
    attachments_block = _attachment_block_for_case(req.case_id, _cases_user_id(user)) if req.case_id else ""
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

    retried = False
    retry_reason: str | None = None
    with traced_workflow(
        "Patient Communication: Empathetic Summary",
        metadata={
            "stage": "7-message",
            "diagnosis": _truncate(str(req.consensus.get("primaryDiagnosis", ""))),
            "urgency": req.consensus.get("urgency", "unknown"),
            "symptoms": _truncate(req.symptoms),
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
MAX_RETRIEVED_CONSULTATION_CHARS = 4000
MAX_RETRIEVE_DOCUMENT_CHARS = 4000
MAX_CONSULTATION_ATTACHMENT_TEXTS = 20
MAX_CONSULTATION_ATTACHMENT_TEXT_CHARS = 5000
MAX_CONSULTATION_CASE_STATE_CHARS = 80000


def _consultation_count(con, user_id: str) -> int:
    row = con.execute(
        "SELECT COUNT(*) AS n FROM consultations WHERE user_id = %s", (user_id,)
    ).fetchone()
    return int(row["n"]) if row else 0


def _assert_consultation_cap(con, user_id: str, user_plan: str) -> None:
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
    case_state: dict[str, Any] | None = None

    @field_validator("attachment_texts")
    @classmethod
    def _bound_attachment_texts(cls, value: list[str]) -> list[str]:
        if len(value) > MAX_CONSULTATION_ATTACHMENT_TEXTS:
            raise ValueError("Too many attachment texts.")
        return [str(text)[:MAX_CONSULTATION_ATTACHMENT_TEXT_CHARS] for text in value]

    @field_validator("case_state")
    @classmethod
    def _bound_case_state(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        encoded = json.dumps(value, ensure_ascii=False)
        if len(encoded) > MAX_CONSULTATION_CASE_STATE_CHARS:
            raise ValueError("Case state is too large.")
        return value


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
        # psycopg3 with autocommit=False opens an implicit transaction on the
        # first execute; the SELECT, UPDATE, and INSERT below all run inside
        # that single transaction and are committed together. A per-user
        # advisory lock serialises the cap check + INSERT against parallel
        # save_consultation calls from the same user — without it two
        # simultaneous Free-tier saves can both pass the cap check and both
        # INSERT, busting FREE_CONSULTATION_CAP. The lock auto-releases on
        # commit/rollback (xact-scoped).
        lock_key = int.from_bytes(
            hashlib.sha256(user_id.encode("utf-8")).digest()[:8],
            "big",
            signed=True,
        )
        con.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))

        case_row = con.execute(
            "SELECT state FROM cases WHERE id = %s AND user_id = %s",
            (req.case_id, user_id),
        ).fetchone()
        if case_row is None:
            raise HTTPException(status_code=404, detail="Case not found.")

        _assert_consultation_cap(con, user_id, plan)

        consultation_id = f"con_{uuid.uuid4().hex[:24]}"
        now = datetime.now(timezone.utc).isoformat()
        case_state = req.case_state if req.case_state is not None else _json_object(case_row["state"])
        case_state_json = json.dumps(case_state, ensure_ascii=False)

        if req.case_state is not None:
            con.execute(
                "UPDATE cases SET state = %s, updated_at = %s WHERE id = %s AND user_id = %s",
                (case_state_json, now, req.case_id, user_id),
            )

        con.execute(
            """
            INSERT INTO consultations
              (id, user_id, case_id, summary, primary_dx, icd_code, urgency, confidence, case_state, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
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
                case_state_json,
                now,
            ),
        )
        con.commit()

        memory_text = build_consultation_memory_text(
            summary=req.summary,
            primary_dx=req.primary_dx,
            icd_code=req.icd_code,
            urgency=req.urgency,
            confidence=req.confidence,
            attachment_texts=req.attachment_texts,
            case_state=case_state,
        )

        try:
            vec = get_embedding_provider().embed(memory_text)
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
                document=memory_text,
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
    except Exception:
        # Make the rollback explicit so failures surface in psycopg's logs.
        # close() in the finally also rolls back, but explicit is clearer.
        try:
            con.rollback()
        except Exception:
            pass
        raise
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
            WHERE user_id = %s
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
                    "document": (
                        h.document[:MAX_RETRIEVE_DOCUMENT_CHARS].rstrip() + "\n[truncated]"
                        if len(h.document) > MAX_RETRIEVE_DOCUMENT_CHARS
                        else h.document
                    ),
                }
                for h in hits
            ]
        }
    finally:
        con.close()


@app.get("/api/patient/consultations/{consultation_id}")
async def get_consultation(
    consultation_id: str,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    """Return a single consultation joined with its case state.

    Powers the /patient/consultations/[id] detail view — lets the UI re-render
    the full seven-stage session (intake, follow-up, council, research,
    consensus, plan, message) as tabs without firing a second `/api/cases/…`.
    """
    user_id = _cases_user_id(user)
    con = _get_db()
    try:
        row = con.execute(
            """
            SELECT c.id, c.case_id, c.user_id, c.summary, c.primary_dx, c.icd_code,
                   c.urgency, c.confidence, c.case_state AS consultation_state, c.created_at,
                   cs.state AS case_state, cs.title AS case_title
            FROM consultations c
            LEFT JOIN cases cs ON cs.id = c.case_id AND cs.user_id = c.user_id
            WHERE c.id = %s
            """,
            (consultation_id,),
        ).fetchone()
        if row is None or (row["user_id"] or "") != user_id:
            raise HTTPException(status_code=404, detail="Consultation not found.")

        case_state = _json_object(row["consultation_state"]) or _json_object(row["case_state"])

        created_at = row["created_at"]
        return {
            "id": row["id"],
            "case_id": row["case_id"],
            "case_title": row["case_title"],
            "summary": row["summary"],
            "primary_dx": row["primary_dx"],
            "icd_code": row["icd_code"],
            "urgency": row["urgency"],
            "confidence": row["confidence"],
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or ""),
            "case_state": case_state,
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
            "SELECT user_id FROM consultations WHERE id = %s", (consultation_id,)
        ).fetchone()
        if row is None or (row["user_id"] or "") != user_id:
            raise HTTPException(status_code=404, detail="Consultation not found.")
        con.execute("DELETE FROM consultations WHERE id = %s", (consultation_id,))
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
            document = (h.document or "").strip()
            if len(document) > MAX_RETRIEVED_CONSULTATION_CHARS:
                document = document[:MAX_RETRIEVED_CONSULTATION_CHARS].rstrip() + "\n[truncated]"
            lines.append(
                f"[{date} · {dx} (confidence {conf}%, {urgency}) · match {score_pct}%]\n{document}"
            )
        lines.append("---")
        return "\n\n".join(lines)
    finally:
        con.close()


# ─────────────────────────────────────────────────────────────────────────────
#  Test attachments (Phase 3.5) — PDF / text upload attached to follow-up
# ─────────────────────────────────────────────────────────────────────────────


def _attachment_block_for_case(
    case_id: str,
    user_id: str,
    question_texts: list[str] | None = None,
) -> str:
    """Read attachments for a case and render as a prompt-safe text block.

    Ownership is enforced at write time by `create_attachment` (the case is
    verified to belong to the requesting user before save). The per-row
    user_id filter below is the single read-time check — sufficient because
    a row exists with `user_id != requesting_user` only if either save's
    ownership check was skipped or somebody wrote rows out-of-band, both of
    which fall back to "filter rejects all rows → empty block".

    Previously this function also ran a `SELECT id FROM cases WHERE id=%s
    AND user_id=%s` upfront. Dropped — that query duplicated the per-row
    filter, added a round-trip per agent stage, and was the dominant cost
    on Cloud Run cold starts (~5–10ms × stages).
    """
    from attachments import format_attachment_block, get_attachment_store

    if not user_id:
        return ""

    con = _get_db()
    try:
        rows = [
            row
            for row in get_attachment_store().list_for_case(con, case_id)
            if row.user_id == user_id
        ]
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
            "SELECT user_id FROM cases WHERE id = %s", (case_id,)
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
        row = con.execute("SELECT user_id FROM cases WHERE id = %s", (case_id,)).fetchone()
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
    from speech import get_speech_provider, SpeechQuotaError, SpeechUnavailableError

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
    except SpeechQuotaError as exc:
        raise HTTPException(
            status_code=429,
            detail={"code": "transcribe_quota", "message": str(exc)},
        ) from exc
    except SpeechUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "transcribe_unavailable", "message": str(exc)},
        ) from exc
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

    from speech import SpeechQuotaError, SpeechUnavailableError

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
    except SpeechQuotaError as exc:
        raise HTTPException(
            status_code=429,
            detail={"code": "synthesize_quota", "message": str(exc)},
        ) from exc
    except SpeechUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "synthesize_unavailable", "message": str(exc)},
        ) from exc
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
