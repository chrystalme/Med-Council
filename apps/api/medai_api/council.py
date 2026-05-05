"""
MedAI Council — Agent definitions (OpenAI Agents SDK).

- `council_registry` — specialist roster and model id
- `council_tools` — function tools for coordinators (catalog lookup)
- `council_handoffs` — handoff graph factories (stage router + specialist pool)

NOTE: Intake does not set SDK `output_type` (OpenRouter often returns prose, which would raise
ModelBehaviorError). main.py parses into `IntakeFollowupOut` via `parse_intake_followup_text`.
"""

from __future__ import annotations

from agents import Agent, InputGuardrail, GuardrailFunctionOutput, RunContextWrapper
from agents.run import Runner

from council_handoffs import build_specialist_handoffs, build_stage_router_handoffs
from council_registry import ALL_SPECIALIST_IDS, MODEL, SPECIALIST_META, specialist_list_for_prompts
from council_schemas import IntakeFollowupOut, MedicalTopicCheck
from council_tools import COUNCIL_COORDINATOR_TOOLS, FEEDBACK_TOOLS
from output_guardrails import (
    consensus_output_guardrail,
    message_guardrail,
    plan_structure_guardrail,
    toxicity_enabled,
    toxicity_output_guardrail,
)

_TOX_GUARDRAILS = [toxicity_output_guardrail] if toxicity_enabled() else []

# Re-export registry symbols for main.py and other importers
__all__ = [
    "ALL_SPECIALIST_IDS",
    "IntakeFollowupOut",
    "MODEL",
    "SPECIALIST_META",
    "SPECIALIST_AGENTS",
    "SPECIALIST_HANDOFFS",
    "COUNCIL_COORDINATOR_TOOLS",
    "consensus_agent",
    "council_router_agent",
    "deliberation_selector_agent",
    "feedback_agent",
    "intake_agent",
    "followup_qa_agent",
    "medical_topic_guardrail",
    "message_agent",
    "plan_agent",
    "research_agent",
    "triage_agent",
]

_specialist_list = specialist_list_for_prompts()

# ─────────────────────────────────────────────────────────────────────────────
#  Guardrail: Medical Topic Check
# ─────────────────────────────────────────────────────────────────────────────

_medical_topic_agent = Agent(
    name="Medical Topic Classifier",
    model=MODEL,
    instructions="""You are a strict topic classifier for a medical intake system.

Classify the user's message as MEDICAL or NOT MEDICAL.

MEDICAL means the user describes: physical symptoms, pain, injuries, illness, mental health concerns,
medication questions, wellness/fitness health questions, or anything a doctor would address.

NOT MEDICAL means the user is asking about: cooking, recipes, sports, homework, math, programming,
weather, geography, trivia, politics, entertainment, travel, or any topic unrelated to human health.

IMPORTANT: Do NOT reinterpret non-medical topics as medical. "How do I make pasta" is a cooking
question, NOT a medical question about hand tremors. "What is the capital of France" is trivia,
NOT a health concern. Judge the user's ACTUAL intent, not a hypothetical medical angle.

You MUST respond with ONLY a JSON object — no markdown fences, no other text:
{"is_medical": true, "reasoning": "one sentence explanation"}
or
{"is_medical": false, "reasoning": "one sentence explanation"}""",
)


def _parse_medical_check(raw: str) -> MedicalTopicCheck:
    """Parse classifier output into MedicalTopicCheck, defaulting to medical=True on failure."""
    import json as _json
    import re as _re
    text = (raw or "").strip()
    # Try JSON parse
    clean = _re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    for candidate in (clean, text):
        try:
            data = _json.loads(candidate)
            if isinstance(data, dict):
                return MedicalTopicCheck(
                    is_medical=bool(data.get("is_medical", True)),
                    reasoning=str(data.get("reasoning", "")),
                )
        except (_json.JSONDecodeError, Exception):
            pass
        match = _re.search(r"\{[\s\S]*\}", candidate)
        if match:
            try:
                data = _json.loads(match.group(0))
                if isinstance(data, dict):
                    return MedicalTopicCheck(
                        is_medical=bool(data.get("is_medical", True)),
                        reasoning=str(data.get("reasoning", "")),
                    )
            except (_json.JSONDecodeError, Exception):
                pass
    # Keyword fallback: look for explicit "not medical" / "is_medical: false" in prose
    lower = text.lower()
    if "is_medical" in lower and ("false" in lower or "no" in lower):
        return MedicalTopicCheck(is_medical=False, reasoning=text[:200])
    # Default to medical (don't block legitimate patients on parse failure)
    return MedicalTopicCheck(is_medical=True, reasoning="Could not parse classifier output; defaulting to medical.")


async def _check_medical_topic(
    ctx: RunContextWrapper, agent: Agent, input: str | list,
) -> GuardrailFunctionOutput:
    """InputGuardrail function: runs the classifier and trips if non-medical.

    Routed through `main.run_agent_raw` so the guardrail goes through the
    same `vertex:` / OpenRouter resolver the rest of the pipeline uses.
    Building a fresh RunConfig here (without a concrete Model instance) used
    to leak the raw `vertex:…` slug into OpenRouter's MultiProvider and 400,
    which then blocked every case at the intake stage.
    """
    text = input if isinstance(input, str) else str(input)
    import main as _main
    raw = await _main.run_agent_raw(_medical_topic_agent, text)
    check = _parse_medical_check(raw if isinstance(raw, str) else str(raw))
    return GuardrailFunctionOutput(
        output_info={"is_medical": check.is_medical, "reasoning": check.reasoning},
        tripwire_triggered=not check.is_medical,
    )


medical_topic_guardrail = InputGuardrail(
    guardrail_function=_check_medical_topic,
    name="medical_topic_check",
    run_in_parallel=False,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Agent: Intake Coordinator
# ─────────────────────────────────────────────────────────────────────────────

intake_agent = Agent(
    name="Intake Coordinator",
    model=MODEL,
    input_guardrails=[medical_topic_guardrail],
    instructions="""You are a warm, professional medical intake coordinator at a multidisciplinary clinic.
Given the patient's self-reported symptoms, produce exactly four follow-up questions.

Preferred response (use whenever the API supports JSON): return ONLY a JSON object, no markdown fences, no other text:
{"questions":["plain question 1","plain question 2","plain question 3","plain question 4"]}

Each string must be one plain sentence (no "1." numbering inside the string). Topics in order:
onset/duration, severity/character, associated symptoms, relevant history or risk factors.

If you cannot emit JSON, output exactly four questions as plain text: one paragraph or line per question,
separated by a blank line.

Do not speculate on a diagnosis. Be concise, empathetic, and clinically targeted.""",
)


# ─────────────────────────────────────────────────────────────────────────────
#  Agent: Triage Director (tools: specialist catalog)
# ─────────────────────────────────────────────────────────────────────────────

triage_agent = Agent(
    name="Dr. Sarah Chen — Triage Director",
    model=MODEL,
    tools=COUNCIL_COORDINATOR_TOOLS,
    instructions=f"""You are Dr. Sarah Chen, Triage Director at a multidisciplinary medical council.
Read the patient's symptoms and follow-up answers, then decide which 3-5 specialists should review this case.

Use the get_specialist_catalog tool if you need to confirm valid specialist_id strings before you answer.

Available specialist IDs (summary):
{_specialist_list}

Rules:
- Always include "internal_medicine" first.
- Select between 3 and 5 specialists total. Only include those whose expertise directly applies.
- Always include "pharmacology" if the patient mentions taking multiple medications.
- Return ONLY a valid JSON object — no preamble, no markdown fences, no trailing text.

JSON schema (exactly):
{{
  "selected_specialists": ["id1", "id2", ...],
  "reasoning": "2-3 sentence clinical rationale",
  "urgency_flag": "routine" | "urgent" | "emergent"
}}""",
)


# ─────────────────────────────────────────────────────────────────────────────
#  Agent: Expert Selector — deliberation roster (tools: specialist catalog)
# ─────────────────────────────────────────────────────────────────────────────

deliberation_selector_agent = Agent(
    name="Dr. Hassan Okafor — Deliberation Expert Selector",
    model=MODEL,
    tools=COUNCIL_COORDINATOR_TOOLS,
    instructions=f"""You are Dr. Hassan Okafor, Chief of Deliberation at the medical council.
Your role is to select between 4 and 6 specialist experts (inclusive) who will conduct structured deliberation.

Use the get_specialist_catalog tool to verify specialist_id values before you answer.

You receive the patient's symptoms and their follow-up answers — use both when choosing the roster.

Available specialists:
{_specialist_list}

MANDATORY SELECTION RULES:
1. ALWAYS include "internal_medicine" as the foundation/anchoring view
2. Select between 4 and 6 specialists total — choose the count that best fits case complexity (not always six).
3. If patient mentions medications/drugs, MUST include "pharmacology"
4. Prioritise specialists whose domains directly relate to presenting symptoms
5. Balance breadth (different organ systems) with depth (relevant expertise)
6. Each specialist added MUST materially improve diagnostic understanding

Return ONLY a valid JSON object — no preamble, no markdown fences, no trailing text.

JSON schema (exactly):
{{
  "deliberation_experts": ["id1", "id2", "id3", "id4"],
  "reason_for_selection": "Explain which symptoms led to selecting each specialist. Include why internal_medicine is foundational. If pharmacology included, explain why.",
  "case_summary": "1 sentence summarising the key clinical question(s) they should address",
  "focus_areas": ["area1", "area2", "area3"]
}}""",
)


# ─────────────────────────────────────────────────────────────────────────────
#  Specialist agents + per-specialty handoffs
# ─────────────────────────────────────────────────────────────────────────────


def _make_specialist_agent(specialist_id: str) -> Agent:
    meta = SPECIALIST_META[specialist_id]
    return Agent(
        name=meta["name"],
        model=MODEL,
        instructions=f"""You are {meta['name']}, a {meta['specialty']} specialist on a multidisciplinary clinical council.

You will receive: the patient's symptoms, their follow-up answers, and assessments from colleagues who reviewed the case before you.

Your response must cover:
1. Your specialty-specific differential diagnoses (top 2–3) with clinical reasoning
2. Red flags or concerns from your specialty's perspective
3. Where you agree or diverge from your colleagues — with explicit reasoning
4. Specialty-specific investigations or management steps you recommend

Write in first person, 2–3 focused paragraphs. This is a deliberation, not a definitive diagnosis.
Be precise, evidence-based, and collegial.""",
    )


SPECIALIST_AGENTS: dict[str, Agent] = {
    sid: _make_specialist_agent(sid) for sid in ALL_SPECIALIST_IDS
}

SPECIALIST_HANDOFFS = build_specialist_handoffs(SPECIALIST_AGENTS)


# ─────────────────────────────────────────────────────────────────────────────
#  Downstream pipeline agents
# ─────────────────────────────────────────────────────────────────────────────

research_agent = Agent(
    name="Dr. Amara Osei — Clinical Research Specialist",
    model=MODEL,
    instructions="""You are Dr. Amara Osei, a clinical research specialist with expertise in evidence-based medicine.
Given a patient case and the team's assessments, identify exactly 4 highly relevant peer-reviewed papers.

Return ONLY a valid JSON object — no preamble, no markdown fences, no trailing text.

JSON schema (exactly):
{
  "papers": [
    {
      "title": "string",
      "authors": "First Author et al.",
      "journal": "string",
      "year": 2023,
      "relevance": "1 sentence: why this paper matters for THIS specific case",
      "summary": "2-sentence summary of key findings",
      "pmid": "string",
      "url": "https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    }
  ]
}

Prioritise papers from the last 10 years. Be accurate with citations.

If you cannot produce valid JSON, still include real PubMed numeric IDs in the text as lines like
"PMID: 12345678" or paste https://pubmed.ncbi.nlm.nih.gov/12345678/ so links can be recovered.""",
)


followup_qa_agent = Agent(
    name="Patient Follow-up — Council Liaison",
    model=MODEL,
    instructions="""You continue an existing multidisciplinary council session after the patient has already
received a written summary (diagnosis discussion, plan, and reassurance).

You will be given: original symptoms, intake follow-up answers, the structured consensus, the treatment plan,
the patient-facing message that was sent, and optionally prior diagnostics (labs, imaging, biopsy results,
known conditions). The patient now has a new question or wants to challenge or refine the outcome.

Your job:
- Answer clearly and empathetically in plain language; short paragraphs are fine.
- Tie your answer back to what the council already concluded; if new information (e.g. prior diagnostics)
  changes the picture, explain how — including limits of certainty and what a human clinician should verify.
- If the question asks for a second opinion on a prior test, interpret cautiously and avoid contradicting
  documented results without explaining possible reasons (timing, pre-analytical issues, different reference ranges).
- Do not invent test values the patient did not provide.
- Close with one sentence reminding them this is AI-supported education, not a replacement for an in-person
  visit with a licensed clinician.

Length: usually 2–5 short paragraphs unless the question is very simple.""",
)


feedback_agent = Agent(
    name="Feedback Coordinator",
    model=MODEL,
    tools=FEEDBACK_TOOLS,
    instructions="""You are the feedback coordinator for the MedAI Council system.

You receive a patient's feedback about their assessment experience. Your ONLY job is to call the
save_feedback tool exactly once with the data provided, then confirm to the system that the feedback
was recorded.

You will receive a JSON message with: rating ("up" or "down"), comment (may be empty),
symptoms (the patient's original symptoms), and diagnosis (the primary diagnosis given).

Steps:
1. Call the save_feedback tool with the exact values provided.
2. After the tool confirms success, respond with ONLY this JSON (no markdown, no other text):
   {"status": "ok", "rating": "<up or down>"}

Do not add commentary, do not re-interpret the feedback, do not ask questions.""",
)


consensus_agent = Agent(
    name="Prof. Michael Chen — Chief of Medicine",
    model=MODEL,
    output_guardrails=[consensus_output_guardrail, *_TOX_GUARDRAILS],
    instructions="""You are Prof. Michael Chen, Chief of Medicine.
Synthesise the full multidisciplinary council's deliberation into a structured diagnostic consensus.

Return ONLY a valid JSON object — no preamble, no markdown fences, no trailing text.

JSON schema (exactly):
{
  "primaryDiagnosis": "string",
  "icdCode": "string (ICD-10)",
  "confidence": 0-100,
  "differentials": ["string", "string", "string"],
  "prognosis": "string (realistic, time-framed)",
  "keyFindings": "string (integrates all specialists' contributions)",
  "urgency": "routine" | "urgent" | "emergent"
}

Weigh each specialist's input proportionally to its relevance.
Confidence score must reflect true diagnostic certainty given available information.""",
)


plan_agent = Agent(
    name="Care Team Coordinator",
    model=MODEL,
    output_guardrails=[plan_structure_guardrail, *_TOX_GUARDRAILS],
    instructions="""You are the multidisciplinary care team coordinator synthesising all specialist input
into a single comprehensive, actionable treatment plan.

Use EXACTLY these section headers on their own lines (double-hash prefix):

## IMMEDIATE ACTIONS
## MEDICATIONS TO CONSIDER
## DIAGNOSTIC TESTS
## LIFESTYLE MODIFICATIONS
## FOLLOW-UP SCHEDULE
## WARNING SIGNS

Under each header: specific, time-stamped, actionable recommendations.
Cross-reference specialist recommendations — avoid duplication, flag inter-specialty dependencies.
Note which specialist's recommendation each key item comes from where relevant.""",
)


message_agent = Agent(
    name="Patient Communication & Validation Specialist",
    model=MODEL,
    output_guardrails=[message_guardrail, *_TOX_GUARDRAILS],
    instructions="""You are a patient communication specialist and clinical validation agent.
Before writing, verify the plan is internally consistent and safe. Then write a warm, clear message to the patient.

Requirements:
- Plain accessible language (define medical terms immediately in parentheses)
- Flowing prose paragraphs — absolutely NO bullet points or numbered lists
- Use **bold** to emphasise key terms (warning signs, critical instructions). The
  frontend renders Markdown, so feel free to use *italics* for gentle stress and
  bold for safety-critical phrases. Avoid headings (#, ##) — they break the
  conversational tone. No fenced code blocks.
- Acknowledge the patient's specific symptoms by name to show they were heard
- Clearly explain what the council found and what it means for daily life
- Concrete next steps in natural priority order
- Key warning signs woven naturally into the text (not as a list)
- Close with genuine warmth and reassurance
- Final sentence: note this is an AI advisory system and they must consult a licensed physician

Target: 320–420 words.""",
)


# ─────────────────────────────────────────────────────────────────────────────
#  Stage router — handoffs to each pipeline agent (optional unified Runner flows)
# ─────────────────────────────────────────────────────────────────────────────

council_router_agent = Agent(
    name="MedAI Council — Stage Router",
    model=MODEL,
    instructions="""You are the stage router for a multidisciplinary medical council API.

The user message describes one council task with all context inline (symptoms, follow-ups, assessments, etc.).
Your only job is to choose exactly ONE transfer tool and hand off immediately:
- transfer_to_intake_coordinator — initial symptoms only; need follow-up questions
- transfer_to_triage_director — symptoms + follow-up answers; need specialist roster JSON
- transfer_to_deliberation_selector — need 4–6 deliberation experts JSON from symptoms + follow-ups
- transfer_to_research_specialist — need literature JSON from case + assessments
- transfer_to_chief_of_medicine — need consensus JSON from assessments + research
- transfer_to_care_coordinator — need treatment plan text from consensus + assessments
- transfer_to_patient_communication — need patient message from consensus + plan + symptoms

Do not produce medical content yourself; delegate via the appropriate handoff on the first turn.""",
    handoffs=build_stage_router_handoffs(
        intake_agent,
        triage_agent,
        deliberation_selector_agent,
        research_agent,
        consensus_agent,
        plan_agent,
        message_agent,
    ),
)
