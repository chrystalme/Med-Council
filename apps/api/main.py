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

# FEEDBACK_SECRET lives in routers/feedback.py. Re-exported here for the
# startup-banner print below; if you change one, change the other or move
# both behind a shared module.
from routers.feedback import FEEDBACK_SECRET  # noqa: E402


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


# _json_object lives in helpers.py; aliased below for the routes still here.


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
from routers import cases as _cases_router  # noqa: E402
from routers import consensus_plan as _consensus_plan_router  # noqa: E402
from routers import consultations as _consultations_router  # noqa: E402
from routers import council as _council_router  # noqa: E402
from routers import feedback as _feedback_router  # noqa: E402
from routers import intake as _intake_router  # noqa: E402
from routers import message as _message_router  # noqa: E402
from routers import meta as _meta_router  # noqa: E402
from routers import research as _research_router  # noqa: E402
from routers import triage as _triage_router  # noqa: E402

app.include_router(_meta_router.router)
app.include_router(_feedback_router.router)
app.include_router(_cases_router.router)
app.include_router(_intake_router.router)
app.include_router(_triage_router.router)
app.include_router(_council_router.router)
app.include_router(_research_router.router)
app.include_router(_consensus_plan_router.router)
app.include_router(_message_router.router)
app.include_router(_consultations_router.router)


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


# Cases CRUD (/api/cases*) lives in routers/cases.py.
# /api/feedback and /feedback/{token} live in routers/feedback.py.
# _cases_user_id, _utc_now, _json_object are now in helpers.py — main.py
# re-aliases them below for the routes still here (consultations, attachments,
# etc.). Drop the aliases when those routes move.

from helpers import (  # noqa: E402
    cases_user_id as _cases_user_id,
    json_object as _json_object,
    utc_now as _utc_now,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Model selector catalog
# ─────────────────────────────────────────────────────────────────────────────

# /api/me and /api/models are registered via routers/meta.py.


# ─────────────────────────────────────────────────────────────────────────────
#  Stage 1 — Intake
# ─────────────────────────────────────────────────────────────────────────────

# /api/intake/followup lives in routers/intake.py.


# Stage 2 — /api/triage and /api/deliberation/select-experts live in routers/triage.py.


# ─────────────────────────────────────────────────────────────────────────────
#  Stage 3 — Physician Council (one call per specialist)
# ─────────────────────────────────────────────────────────────────────────────

# Stage 3 — /api/council/specialist and /api/council/physician live in routers/council.py.
# _council_context_block lives there too.


# ─────────────────────────────────────────────────────────────────────────────
#  Stage 4 — Research
# ─────────────────────────────────────────────────────────────────────────────

# Stage 4 — /api/research lives in routers/research.py.


# Stages 5 + 6 — /api/consensus and /api/plan live in routers/consensus_plan.py.


# Stage 7 — /api/message and /api/message/followup live in routers/message.py.


# Patient memory (Phase 3) — consultations CRUD + retrieval live in
# routers/consultations.py. Constants (FREE_CONSULTATION_CAP, etc.) live
# alongside the routes.


# _retrieve_patient_context moved to case_context.py; aliased below.
from case_context import retrieve_patient_context as _retrieve_patient_context  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
#  Test attachments (Phase 3.5) — PDF / text upload attached to follow-up
# ─────────────────────────────────────────────────────────────────────────────


# _attachment_block_for_case moved to case_context.py; aliased below.
from case_context import attachment_block_for_case as _attachment_block_for_case  # noqa: E402


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
