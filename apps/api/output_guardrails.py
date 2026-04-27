"""
Output guardrails for the clinical-output agents (consensus, plan, message).

Mirrors the input-guardrail pattern at council.py:46-135 — same module shape,
same `GuardrailFunctionOutput(output_info=..., tripwire_triggered=...)` return.
On trip the SDK raises `OutputGuardrailTripwireTriggered`, which the FastAPI
exception handler in main.py translates to HTTP 422 with a structured
`{code, subcode, message, stage, guardrail}` detail.

Each guardrail records a `custom_span` so the trip outcome shows up under the
existing OpenAI-Agents trace tree (already forwarded to Langfuse).

Feature flags:
- ``OUTPUT_TOXICITY_GUARDRAIL=1``      — enable the toxicity classifier guardrail
                                         on all three agents (extra LLM call per stage).
- ``MESSAGE_HALLUCINATION_CHECK=1``    — enable the diagnosis-introduces-unknown check
                                         on the message guardrail (heuristic; FP rate
                                         non-zero, hence default off).
- ``STRICT_ICD=1``                     — require a non-empty ICD-10 code on consensus.
                                         Default lenient: empty string is allowed.
"""

from __future__ import annotations

import json as _json
import logging
import os
import re
from typing import Any

from agents import (
    Agent,
    GuardrailFunctionOutput,
    OutputGuardrail,
    RunContextWrapper,
)
from agents.tracing import custom_span
from pydantic import ValidationError

from council_registry import MODEL
from council_schemas import ConsensusOut

log = logging.getLogger("medai.output_guardrails")


# ─────────────────────────────────────────────────────────────────────────────
#  Feature flags (read once at module load)
# ─────────────────────────────────────────────────────────────────────────────


def toxicity_enabled() -> bool:
    return os.environ.get("OUTPUT_TOXICITY_GUARDRAIL", "0") == "1"


def message_hallucination_enabled() -> bool:
    return os.environ.get("MESSAGE_HALLUCINATION_CHECK", "0") == "1"


def strict_icd() -> bool:
    return os.environ.get("STRICT_ICD", "0") == "1"


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _annotate(span: Any, **fields: Any) -> None:
    """Attach key/value fields to a custom span's data dict.

    The SDK has no public ``span.set_data(...)`` — data is stored on
    ``span.span_data.data``. ``NoOpSpan`` (returned when tracing is disabled,
    e.g. in tests) also has ``span_data``, so this is safe in all paths.
    """
    try:
        bag = getattr(span.span_data, "data", None)
        if isinstance(bag, dict):
            bag.update(fields)
    except Exception:  # pragma: no cover - tracing must never break a guardrail
        pass


def _coerce_str(output: Any) -> str:
    if isinstance(output, str):
        return output
    if hasattr(output, "model_dump_json"):
        try:
            return output.model_dump_json()
        except Exception:
            pass
    return str(output) if output is not None else ""


def _strip_json_fences(raw: str) -> str:
    return re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()


def _try_parse_json(raw: str) -> Any:
    """Tolerant JSON parse — strips fences and falls back to object-extraction.

    Mirrors `parse_json` in main.py so guardrails can run before the route
    handler parses the agent output a second time, without importing main.
    """
    text = (raw or "").strip()
    clean = _strip_json_fences(text)
    for candidate in (clean, text):
        try:
            return _json.loads(candidate)
        except _json.JSONDecodeError:
            pass
        match = re.search(r"\{[\s\S]*\}", candidate)
        if match:
            try:
                return _json.loads(match.group(0))
            except _json.JSONDecodeError:
                continue
    raise ValueError("Could not extract JSON object from output")


# ─────────────────────────────────────────────────────────────────────────────
#  1) Consensus guardrail — schema + clinical compliance + ICD-10 format
# ─────────────────────────────────────────────────────────────────────────────


# ICD-10 format: one letter, two digits, optional decimal + 1-4 alphanumeric chars.
# Catches obvious garbage ("MRI-Brain", "Migraine"); does NOT catch plausible-but-fake
# codes (Z99.999) — that would require an offline ICD-10 dictionary. Out of scope.
_ICD10_RE = re.compile(r"^[A-Z][0-9]{2}(\.[0-9A-Z]{1,4})?$")


def _consensus_failure_info(failed_check: str, code: str, message: str, **extra: Any) -> dict:
    return {
        "failed_check": failed_check,
        "code": code,
        "message": message,
        "passed": False,
        **extra,
    }


async def _check_consensus_output(
    ctx: RunContextWrapper, agent: Agent, output: Any,
) -> GuardrailFunctionOutput:
    raw = _coerce_str(output)

    with custom_span("output_guardrail.consensus", data={"agent": getattr(agent, "name", "?")}) as span:
        # 1. Parse
        try:
            data = _try_parse_json(raw)
        except ValueError as exc:
            info = _consensus_failure_info(
                "parse",
                "consensus_unparseable",
                "Consensus stage returned a response that wasn't parseable JSON.",
                raw=raw[:400],
                error=str(exc)[:200],
            )
            _annotate(span, passed=False)
            _annotate(span, failed_check="parse")
            return GuardrailFunctionOutput(output_info=info, tripwire_triggered=True)

        if not isinstance(data, dict):
            info = _consensus_failure_info(
                "parse",
                "consensus_unparseable",
                "Consensus stage returned a JSON value that wasn't an object.",
                raw=raw[:400],
            )
            _annotate(span, passed=False)
            _annotate(span, failed_check="parse")
            return GuardrailFunctionOutput(output_info=info, tripwire_triggered=True)

        # 2. Schema validate (covers urgency Literal + confidence range + required fields)
        try:
            validated = ConsensusOut.model_validate(data)
        except ValidationError as exc:
            info = _consensus_failure_info(
                "schema",
                "consensus_schema_invalid",
                "Consensus output failed schema validation.",
                errors=[
                    {"loc": ".".join(str(x) for x in e["loc"]), "msg": e["msg"]}
                    for e in exc.errors()[:5]
                ],
                raw=raw[:400],
            )
            _annotate(span, passed=False)
            _annotate(span, failed_check="schema")
            return GuardrailFunctionOutput(output_info=info, tripwire_triggered=True)

        # 3. ICD-10 format check (lenient by default — empty string is allowed)
        icd = (validated.icdCode or "").strip()
        if icd:
            if not _ICD10_RE.match(icd):
                info = _consensus_failure_info(
                    "icd",
                    "consensus_icd_invalid",
                    f"Consensus icdCode {icd!r} does not match the ICD-10 format.",
                    icdCode=icd,
                )
                _annotate(span, passed=False)
                _annotate(span, failed_check="icd")
                return GuardrailFunctionOutput(output_info=info, tripwire_triggered=True)
        elif strict_icd():
            info = _consensus_failure_info(
                "icd",
                "consensus_icd_invalid",
                "Consensus icdCode was empty and STRICT_ICD is enabled.",
                icdCode="",
            )
            _annotate(span, passed=False)
            _annotate(span, failed_check="icd")
            return GuardrailFunctionOutput(output_info=info, tripwire_triggered=True)

        _annotate(span, passed=True)
        _annotate(span, urgency=validated.urgency)
        _annotate(span, confidence=validated.confidence)
        return GuardrailFunctionOutput(
            output_info={
                "checks": ["parse", "schema", "icd"],
                "passed": True,
                "urgency": validated.urgency,
                "icdCode": icd,
                "confidence": validated.confidence,
            },
            tripwire_triggered=False,
        )


consensus_output_guardrail = OutputGuardrail(
    guardrail_function=_check_consensus_output,
    name="consensus_output_check",
)


# ─────────────────────────────────────────────────────────────────────────────
#  2) Plan structure guardrail
# ─────────────────────────────────────────────────────────────────────────────


# Headers exactly as they appear in `plan_agent` instructions (council.py:376-381).
_PLAN_HEADERS = (
    "IMMEDIATE ACTIONS",
    "MEDICATIONS TO CONSIDER",
    "DIAGNOSTIC TESTS",
    "LIFESTYLE MODIFICATIONS",
    "FOLLOW-UP SCHEDULE",
    "WARNING SIGNS",
)
# Allow optional trailing colon and surrounding whitespace; case-insensitive.
_PLAN_HEADER_RES = tuple(
    re.compile(rf"(?im)^\s*##\s+{re.escape(label)}\s*:?\s*$") for label in _PLAN_HEADERS
)
# Trip when fewer than this many headers match. 5/6 accommodates the model
# legitimately collapsing MEDICATIONS+LIFESTYLE when no medications are warranted.
_PLAN_HEADER_MIN_MATCHES = 5


async def _check_plan_structure(
    ctx: RunContextWrapper, agent: Agent, output: Any,
) -> GuardrailFunctionOutput:
    text = _coerce_str(output)

    with custom_span("output_guardrail.plan", data={"agent": getattr(agent, "name", "?")}) as span:
        found: list[str] = []
        missing: list[str] = []
        for label, pat in zip(_PLAN_HEADERS, _PLAN_HEADER_RES):
            (found if pat.search(text) else missing).append(label)

        passed = len(found) >= _PLAN_HEADER_MIN_MATCHES

        if not passed:
            info = {
                "failed_check": "headers",
                "code": "plan_structure_invalid",
                "message": (
                    "Plan output is missing required section headers "
                    f"({len(found)}/{len(_PLAN_HEADERS)} found)."
                ),
                "missing_headers": missing,
                "found": found,
                "raw": text[:400],
                "passed": False,
            }
            _annotate(span, passed=False)
            _annotate(span, missing_headers=missing)
            return GuardrailFunctionOutput(output_info=info, tripwire_triggered=True)

        _annotate(span, passed=True)
        _annotate(span, found=len(found))
        return GuardrailFunctionOutput(
            output_info={
                "checks": ["headers"],
                "passed": True,
                "found": found,
                "missing_headers": missing,
            },
            tripwire_triggered=False,
        )


plan_structure_guardrail = OutputGuardrail(
    guardrail_function=_check_plan_structure,
    name="plan_structure_check",
)


# ─────────────────────────────────────────────────────────────────────────────
#  3) Message guardrail — disclaimer (always on) + diagnosis hallucination (gated)
# ─────────────────────────────────────────────────────────────────────────────


_AI_NOUN_RE = re.compile(r"\b(ai|artificial[\s-]intelligence)\b", re.IGNORECASE)
_CLINICIAN_NOUN_RE = re.compile(
    r"\b(physician|doctor|clinician|healthcare provider|medical professional)\b",
    re.IGNORECASE,
)
_RECOMMEND_VERB_RE = re.compile(r"\b(consult|see|speak|talk)\b", re.IGNORECASE)

# Heuristic noun-phrase pattern for diagnosis-like terms in patient message.
# Catches multi-syllable terms ending in -itis/-osis/-emia/-pathy/-oma; far from
# perfect (will miss "stroke", "heart attack"). FP-prone — hence feature-gated.
_DX_TOKEN_RE = re.compile(
    r"\b[A-Za-z]{4,}(?:itis|osis|emia|pathy|oma|gia|algia|opia)\b",
    re.IGNORECASE,
)


def _disclaimer_present(text: str) -> bool:
    """True if the closing sentences contain the AI-disclaimer triad.

    The agent's instructions (council.py) say the *final sentence* must mention
    that this is an AI advisory system and the patient must consult a licensed
    physician. Models often spread the same idea across the last 1–2 sentences,
    so we check the closing **two sentences** for an AI-noun + clinician-noun +
    a recommendation verb. If the message is very long the trailing 30% is
    used as a floor to keep wide messages from sliding the disclaimer out of
    range.
    """
    if not text:
        return False
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    tail_sentences = " ".join(sentences[-2:]) if sentences else ""
    floor_start = max(0, int(len(text) * 0.7))
    tail = text[floor_start:]
    # Use whichever window is longer — sentences are usually richer; the
    # 30% floor catches degenerate messages that lack sentence punctuation.
    window = tail_sentences if len(tail_sentences) >= len(tail) else tail
    return bool(
        _AI_NOUN_RE.search(window)
        and _CLINICIAN_NOUN_RE.search(window)
        and _RECOMMEND_VERB_RE.search(window)
    )


def _normalise_dx(s: str) -> set[str]:
    """Return a set of lowercase tokens (≥4 chars) extracted from a diagnosis string."""
    return {tok.lower() for tok in re.findall(r"[A-Za-z]{4,}", s or "")}


def _diagnosis_intruders(message_text: str, consensus: dict) -> list[str]:
    """Return diagnosis-like tokens in the message not anchored in consensus dx terms."""
    primary = consensus.get("primaryDiagnosis") or consensus.get("primary_diagnosis") or ""
    diffs = consensus.get("differentials") or []
    if not isinstance(diffs, list):
        diffs = []
    allowed = _normalise_dx(primary)
    for d in diffs:
        if isinstance(d, str):
            allowed |= _normalise_dx(d)
    candidates = {m.group(0).lower() for m in _DX_TOKEN_RE.finditer(message_text)}
    intruders = sorted(c for c in candidates if c not in allowed)
    return intruders


async def _check_message(
    ctx: RunContextWrapper, agent: Agent, output: Any,
) -> GuardrailFunctionOutput:
    text = _coerce_str(output)

    with custom_span("output_guardrail.message", data={"agent": getattr(agent, "name", "?")}) as span:
        # Disclaimer check — always on.
        if not _disclaimer_present(text):
            info = {
                "failed_check": "disclaimer",
                "code": "message_disclaimer_missing",
                "message": (
                    "Patient-facing message did not end with the required AI advisory "
                    "disclaimer. The closing sentences must mention an AI system AND "
                    "recommend consulting a licensed physician."
                ),
                "raw": text[-400:],
                "passed": False,
            }
            _annotate(span, passed=False)
            _annotate(span, failed_check="disclaimer")
            return GuardrailFunctionOutput(output_info=info, tripwire_triggered=True)

        # Diagnosis hallucination check — gated behind MESSAGE_HALLUCINATION_CHECK.
        if message_hallucination_enabled():
            consensus = None
            if ctx is not None and isinstance(ctx.context, dict):
                consensus = ctx.context.get("consensus")
            if isinstance(consensus, dict):
                intruders = _diagnosis_intruders(text, consensus)
                if intruders:
                    info = {
                        "failed_check": "hallucination",
                        "code": "message_introduces_unknown_diagnosis",
                        "message": (
                            "Patient-facing message introduces diagnosis-like terms not "
                            "present in the consensus."
                        ),
                        "unknown": intruders[:10],
                        "raw": text[:400],
                        "passed": False,
                    }
                    _annotate(span, passed=False)
                    _annotate(span, failed_check="hallucination")
                    return GuardrailFunctionOutput(output_info=info, tripwire_triggered=True)

        _annotate(span, passed=True)
        return GuardrailFunctionOutput(
            output_info={"checks": ["disclaimer", "hallucination"], "passed": True},
            tripwire_triggered=False,
        )


message_guardrail = OutputGuardrail(
    guardrail_function=_check_message,
    name="message_compliance_check",
)


# ─────────────────────────────────────────────────────────────────────────────
#  4) Toxicity / refusal guardrail (shared, gated)
# ─────────────────────────────────────────────────────────────────────────────


# Cheapest free-tier model. Pinned regardless of the user's plan so a Pro user
# choosing GPT-5 doesn't bill the toxicity classifier at GPT-5 rates.
_TOXICITY_MODEL = "vertex:google/gemini-2.5-flash-lite"


_toxicity_agent = Agent(
    name="Output Safety Classifier",
    model=MODEL,
    instructions="""You are a strict safety classifier for clinical assistant outputs.

Classify the assistant's output as SAFE or UNSAFE.

UNSAFE means: contains harmful content (instructions to harm self/others, illegal activity);
is a refusal that contradicts the medical scope (e.g. "I cannot help with medical advice");
is off-topic for clinical assistance; contains slurs or abuse.

SAFE means: a normal clinical answer or summary, even if it discusses sensitive topics
(self-harm, drugs, end-of-life) in an appropriate, professional tone.

Respond with ONLY a JSON object:
{"is_safe": true, "category": "safe", "reasoning": "one sentence"}
or
{"is_safe": false, "category": "harmful|refusal|off_topic|slur", "reasoning": "one sentence"}""",
)


def _parse_toxicity(raw: str) -> tuple[bool, str, str]:
    """Parse classifier output. Default to SAFE on parse failure (don't block on bug)."""
    try:
        data = _try_parse_json(raw)
    except ValueError:
        return True, "safe", "Could not parse classifier output; defaulting to safe."
    if not isinstance(data, dict):
        return True, "safe", "Classifier returned non-object; defaulting to safe."
    is_safe = bool(data.get("is_safe", True))
    category = str(data.get("category", "safe"))
    reasoning = str(data.get("reasoning", ""))
    return is_safe, category, reasoning


async def _check_toxicity(
    ctx: RunContextWrapper, agent: Agent, output: Any,
) -> GuardrailFunctionOutput:
    text = _coerce_str(output)

    with custom_span("output_guardrail.toxicity", data={"agent": getattr(agent, "name", "?")}) as span:
        # Lazy import to dodge the council → main → council import cycle that
        # already exists for the input guardrail (council.py:122).
        import main as _main

        try:
            raw = await _main.run_agent_raw(
                _toxicity_agent, text, model=_TOXICITY_MODEL,
            )
        except Exception as exc:
            # Don't block real outputs on classifier infrastructure failure.
            log.warning("toxicity classifier failed; passing output through (%s)", exc)
            _annotate(span, passed=True)
            _annotate(span, classifier_error=str(exc)[:200])
            return GuardrailFunctionOutput(
                output_info={
                    "checks": ["toxicity"],
                    "passed": True,
                    "classifier_error": str(exc)[:200],
                },
                tripwire_triggered=False,
            )

        is_safe, category, reasoning = _parse_toxicity(_coerce_str(raw))

        if not is_safe:
            info = {
                "failed_check": "toxicity",
                "code": "toxicity_flagged",
                "message": (
                    "Output classifier flagged this stage's output as unsafe."
                ),
                "category": category,
                "reasoning": reasoning,
                "passed": False,
            }
            _annotate(span, passed=False)
            _annotate(span, category=category)
            return GuardrailFunctionOutput(output_info=info, tripwire_triggered=True)

        _annotate(span, passed=True)
        _annotate(span, category=category)
        return GuardrailFunctionOutput(
            output_info={
                "checks": ["toxicity"],
                "passed": True,
                "category": category,
                "reasoning": reasoning,
            },
            tripwire_triggered=False,
        )


toxicity_output_guardrail = OutputGuardrail(
    guardrail_function=_check_toxicity,
    name="output_toxicity_check",
)


__all__ = [
    "ConsensusOut",
    "consensus_output_guardrail",
    "plan_structure_guardrail",
    "message_guardrail",
    "toxicity_output_guardrail",
    "toxicity_enabled",
    "message_hallucination_enabled",
    "strict_icd",
    # Functions exposed for unit tests
    "_check_consensus_output",
    "_check_plan_structure",
    "_check_message",
    "_check_toxicity",
    "_disclaimer_present",
    "_diagnosis_intruders",
]
