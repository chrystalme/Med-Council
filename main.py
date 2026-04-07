"""
MedAI Council — FastAPI Backend
Inference : OpenRouter  →  nvidia/nemotron-3-super-120b-a12b
Tracing   : OpenAI Agents SDK  →  platform.openai.com/traces (export key separate from OpenRouter)

Environment variables required:
    OPENROUTER_API_KEY   — for model inference (https://openrouter.ai/keys)
    OPENAI_API_KEY       — for tracing export only  (https://platform.openai.com/api-keys)

Run:
    uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

import json
import os
import re
from contextlib import asynccontextmanager, contextmanager
from typing import Annotated, Any
from dotenv import load_dotenv
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, field_validator

from agents import (
    Agent,
    set_default_openai_api,
    set_default_openai_client,
    set_tracing_export_api_key,
)
from agents.models.multi_provider import MultiProvider
from agents.run import Runner
from agents.run_config import RunConfig
from agents.tracing import trace as workflow_trace
from agents.tracing.setup import get_trace_provider

from council_schemas import (
    IntakeFollowupOut,
    PatientSymptomsIn,
    parse_intake_followup_text,
    parse_research_papers,
)

from council import (
    ALL_SPECIALIST_IDS,
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

load_dotenv(override=True)

# OpenRouter models like nvidia/... use an unknown MultiProvider prefix — pass full ID to the client.
_council_model_provider: MultiProvider | None = None


def _flush_sdk_traces() -> None:
    """Push queued traces immediately (BatchTraceProcessor defaults to ~5s delay)."""
    try:
        get_trace_provider().force_flush()
    except Exception:
        pass


@contextmanager
def traced_workflow(name: str):
    """OpenAI Agents SDK workflow trace + immediate export flush for the dashboard."""
    with workflow_trace(name):
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

    print("✓ Inference  → OpenRouter  (nvidia/nemotron-3-super-120b-a12b)")
    print("✓ Tracing    → platform.openai.com/traces  (OpenAI Agents SDK exporter)")

    yield


app = FastAPI(title="MedAI Council", version="3.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def run_agent_raw(agent: Agent, prompt: str) -> Any:
    """Run an agent and return `final_output` (str or Pydantic model when `output_type` is set)."""
    rc = RunConfig(model_provider=_council_model_provider) if _council_model_provider else RunConfig()
    result = await Runner.run(agent, prompt, run_config=rc)
    return result.final_output


async def run_agent(agent: Agent, prompt: str) -> str:
    """Run an agent and return its final output as a string."""
    output = await run_agent_raw(agent, prompt)
    if isinstance(output, str):
        return output
    return output.model_dump_json() if hasattr(output, "model_dump_json") else str(output)


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


class TriageIn(BaseModel):
    symptoms: str
    followup_answers: str

class SpecialistIn(BaseModel):
    specialist_id: str
    symptoms: str
    followup_answers: str
    prior_assessments: list[dict]
    council_context: str = ""


class PhysicianIn(BaseModel):
    """Alias for SpecialistIn to match frontend naming"""
    physician_id: str
    symptoms: str
    followup_answers: str
    prior_assessments: list[dict]
    council_context: str = ""


class ResearchIn(BaseModel):
    symptoms: str
    followup_answers: str
    assessments: list[dict]


class ConsensusIn(BaseModel):
    symptoms: str
    followup_answers: str
    assessments: list[dict]
    research: list[dict]


class PlanIn(BaseModel):
    symptoms: str
    followup_answers: str
    consensus: dict
    assessments: list[dict]


class MessageIn(BaseModel):
    symptoms: str
    consensus: dict
    plan: str


class PatientFollowUpIn(BaseModel):
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

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "MedAI Council",
        "version": "3.0.0",
        "model": "nvidia/nemotron-3-super-120b-a12b",
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
#  Stage 1 — Intake
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/intake/followup")
async def intake_followup(req: SymptomsIn):
    with traced_workflow("Intake Follow-up Questions"):
        raw_text = await run_agent(
            intake_agent,
            f"Patient self-reports: {req.symptoms}",
        )
    try:
        return {"questions": _format_intake_questions_for_api(raw_text)}
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


# ─────────────────────────────────────────────────────────────────────────────
#  Stage 2 — Triage
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/triage")
async def triage(req: TriageIn):
    prompt = (
        f"Patient symptoms: {req.symptoms}\n\n"
        f"Patient follow-up responses: {req.followup_answers}"
    )
    with traced_workflow("Triage: Specialist Selection"):
        raw = await run_agent(triage_agent, prompt)

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
async def select_deliberation_experts(req: TriageIn):
    """Select 4–6 expert specialists for structured deliberation (symptoms + follow-up answers)."""
    prompt = (
        f"Patient symptoms: {req.symptoms}\n\n"
        f"Patient follow-up responses: {req.followup_answers}"
    )
    with traced_workflow("Expert Selection for Deliberation"):
        raw = await run_agent(deliberation_selector_agent, prompt)

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
async def council_specialist(req: SpecialistIn):
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
    prompt = (
        f"Patient symptoms: {req.symptoms}\n\n"
        f"Patient follow-up responses: {req.followup_answers}"
        f"{ctx}"
        f"{prior_block}"
    )

    specialist_name = SPECIALIST_META[req.specialist_id]["name"]
    with traced_workflow(f"Specialist Assessment: {specialist_name}"):
        assessment = await run_agent(SPECIALIST_AGENTS[req.specialist_id], prompt)
    return {
        "specialist": {"id": req.specialist_id, **SPECIALIST_META[req.specialist_id]},
        "assessment": assessment,
    }


@app.post("/api/council/physician")
async def council_physician(req: PhysicianIn):
    """Alias for council_specialist to match frontend naming (physician_id instead of specialist_id)"""
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
    prompt = (
        f"Patient symptoms: {req.symptoms}\n\n"
        f"Patient follow-up responses: {req.followup_answers}"
        f"{ctx}"
        f"{prior_block}"
    )

    specialist_name = SPECIALIST_META[req.physician_id]["name"]
    with traced_workflow(f"Specialist Assessment: {specialist_name}"):
        assessment = await run_agent(SPECIALIST_AGENTS[req.physician_id], prompt)
    return {
        "specialist": {"id": req.physician_id, **SPECIALIST_META[req.physician_id]},
        "assessment": assessment,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Stage 4 — Research
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/research")
async def research(req: ResearchIn):
    assessments_text = "\n\n".join(
        f"{a['name']} ({a['specialty']}):\n{a['assessment']}" for a in req.assessments
    )
    prompt = (
        f"Patient symptoms: {req.symptoms}\n\n"
        f"Follow-up responses: {req.followup_answers}\n\n"
        f"Team assessments:\n{assessments_text}"
    )
    with traced_workflow("Research: Evidence-Based Paper Selection"):
        raw = await run_agent(research_agent, prompt)

    papers, parse_warning = parse_research_papers(raw)

    # Failsafe: if the model didn't return a usable papers array (or produced narrative-only output),
    # fetch real PubMed links based on the case text so the UI always has actionable references.
    has_any_links = any(bool((p or {}).get("url")) for p in (papers or []))
    if not has_any_links:
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
async def consensus(req: ConsensusIn):
    assessments_text = "\n\n".join(
        f"{a['name']} ({a['specialty']}):\n{a['assessment']}" for a in req.assessments
    )
    research_text = "\n".join(
        f"• {r.get('title','')} ({r.get('year','')}): {r.get('summary','')}"
        for r in req.research
    )
    prompt = (
        f"Patient symptoms: {req.symptoms}\n\n"
        f"Follow-up responses: {req.followup_answers}\n\n"
        f"Specialist assessments:\n{assessments_text}\n\n"
        f"Supporting research:\n{research_text}"
    )
    with traced_workflow("Consensus: Integrating Multidisciplinary Assessment"):
        raw = await run_agent(consensus_agent, prompt)

    try:
        data = parse_json(raw)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"consensus": data}


# ─────────────────────────────────────────────────────────────────────────────
#  Stage 6 — Treatment Plan
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/plan")
async def plan(req: PlanIn):
    assessments_text = "\n\n".join(
        f"{a['name']} ({a['specialty']}):\n{a['assessment']}" for a in req.assessments
    )
    prompt = (
        f"Diagnosis: {json.dumps(req.consensus)}\n\n"
        f"Patient symptoms: {req.symptoms}\n\n"
        f"Follow-up responses: {req.followup_answers}\n\n"
        f"Specialist findings:\n{assessments_text}"
    )
    with traced_workflow("Treatment Plan: Multi-Specialty Coordination"):
        plan_text = await run_agent(plan_agent, prompt)
    return {"plan": plan_text}


# ─────────────────────────────────────────────────────────────────────────────
#  Stage 7 — Patient Message
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/message")
async def patient_message(req: MessageIn):
    prompt = (
        f"Primary diagnosis: {req.consensus.get('primaryDiagnosis')} "
        f"(confidence {req.consensus.get('confidence')}%, {req.consensus.get('urgency')} urgency)\n"
        f"ICD code: {req.consensus.get('icdCode', '')}\n"
        f"Prognosis: {req.consensus.get('prognosis')}\n"
        f"Key findings: {req.consensus.get('keyFindings')}\n\n"
        f"Treatment plan:\n{req.plan}\n\n"
        f"Original patient symptoms: {req.symptoms}"
    )
    with traced_workflow("Patient Communication: Empathetic Summary"):
        message = await run_agent(message_agent, prompt)
    return {"message": message}


@app.post("/api/message/followup")
async def patient_message_followup(req: PatientFollowUpIn):
    """Answer patient questions after the final message; optional prior diagnostics for context."""
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
    with traced_workflow("Patient Follow-up Q&A"):
        reply = await run_agent(followup_qa_agent, prompt)
    return {"reply": reply}
