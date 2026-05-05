"""Agent execution runtime: model-provider routing, retry, tracing, JSON parsing.

Extracted from main.py so the route handlers can stay focused on HTTP
plumbing. The module owns two pieces of mutable state:

* `_council_model_provider` — MultiProvider pointed at OpenRouter (used
  for GPT-5 only).
* `_vertex_model_provider` — direct OpenAI-compatible provider for the
  Vertex AI in-house models (free-tier default + Pro Gemini/Claude/Llama).

Both are wired during FastAPI's `lifespan` startup via the
`set_providers` setter — they live here, not in main.py, so any module
that needs to run an agent can `from agent_runtime import run_agent`
without pulling main.py's full dependency chain.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import contextmanager
from typing import Any

from agents import Agent
from agents.models.multi_provider import MultiProvider
from agents.run import Runner
from agents.run_config import RunConfig
from agents.tracing import trace as workflow_trace
from agents.tracing.setup import get_trace_provider
from fastapi import HTTPException
from pydantic import BaseModel

from .council_registry import resolve_model
from .council_schemas import IntakeFollowupOut, parse_intake_followup_text
from .langfuse_tracing import flush_langfuse, langfuse_attributes

log = logging.getLogger("medai.runtime")


# ── Provider state ──────────────────────────────────────────────────────────
# Populated during FastAPI lifespan startup via set_providers().

_council_model_provider: MultiProvider | None = None
_vertex_model_provider: "_DirectOpenAICompatibleProvider | None" = None

_VERTEX_ROUTING_PREFIX = "vertex:"


def set_providers(
    *,
    council: MultiProvider | None,
    vertex: "_DirectOpenAICompatibleProvider | None",
) -> None:
    """Install the routing providers. Called once during FastAPI lifespan."""
    global _council_model_provider, _vertex_model_provider
    _council_model_provider = council
    _vertex_model_provider = vertex


def get_vertex_provider() -> "_DirectOpenAICompatibleProvider | None":
    """Read-only accessor used by lifespan startup logging."""
    return _vertex_model_provider


# ── Provider class ──────────────────────────────────────────────────────────


class _DirectOpenAICompatibleProvider:
    """Minimal ModelProvider that sends the slug to the client verbatim.

    MultiProvider tries to be clever about `openai/…` / `anthropic/…` slugs,
    which breaks OpenAI-compatible endpoints (Groq, Together, …) that expect
    the full slug intact. This provider always instantiates
    `OpenAIChatCompletionsModel(model=slug, openai_client=client)` — no parsing.
    """

    def __init__(self, client, *, default_model: str):
        from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

        self._client = client
        self._default_model = default_model
        self._ModelClass = OpenAIChatCompletionsModel

    def get_model(self, model_name):
        name = model_name or self._default_model
        return self._ModelClass(model=name, openai_client=self._client)


# ── Tracing helpers ─────────────────────────────────────────────────────────


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
    flush_langfuse()


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
    trace_metadata = _coerce_trace_metadata(metadata)
    with langfuse_attributes(
        session_id=group_id,
        metadata=trace_metadata,
        tags=[trace_metadata["stage"]] if "stage" in trace_metadata else None,
    ):
        with workflow_trace(name, group_id=group_id, metadata=trace_metadata):
            yield
    _flush_sdk_traces()


# ── Provider error classification ───────────────────────────────────────────


_TRANSIENT_PROVIDER_MARKERS = (
    "524",                 # Cloudflare origin timeout (common on free OpenRouter models)
    "502",                 # Bad gateway
    "503",                 # Service unavailable
    "504",                 # Gateway timeout
    "provider returned error",
    "no choices",
    "upstream",
    "timeout",
    "timed out",
    "rate limit",
    "overloaded",
    "try again",
)


def _is_transient_provider_error(exc: BaseException) -> bool:
    """Detect OpenRouter/provider hiccups that are worth retrying."""
    msg = (str(exc) or "").lower()
    if any(marker in msg for marker in _TRANSIENT_PROVIDER_MARKERS):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    return status in {502, 503, 504, 524, 429}


def _is_bad_model_error(exc: BaseException) -> bool:
    """HTTP 400 from the provider about the model id — retrying won't help.

    Matches OpenRouter's "not a valid model ID" and OpenAI/Groq's equivalents
    so the caller can surface a structured "pick another model" prompt.
    """
    msg = str(exc).lower()
    if "not a valid model" in msg or "model not found" in msg or "invalid model" in msg:
        return True
    if "unknown model" in msg:
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if status == 400 and "model" in msg:
        return True
    return False


# ── Run helpers ─────────────────────────────────────────────────────────────


def _resolve_runtime_model(agent: Agent, model: str | None):
    """Pick the provider + concrete model for this run.

    The `vertex:` routing prefix on a slug steers the call to the Vertex client.
    Everything else stays on OpenRouter. Falls through to the agent's bound
    model if no per-run override is supplied.

    For the Vertex path we construct an `OpenAIChatCompletionsModel` instance
    here and return it as the `model` value for RunConfig. This bypasses the
    SDK's provider-lookup path — which we observed does NOT always respect
    `RunConfig.model_provider` when the agent's own `.model` string has an
    unknown prefix, causing the raw `vertex:…` slug to leak into OpenRouter.
    RunConfig accepts a concrete Model instance and uses it directly.
    """
    effective = model if model else getattr(agent, "model", None)
    if isinstance(effective, str) and effective.startswith(_VERTEX_ROUTING_PREFIX):
        clean = effective[len(_VERTEX_ROUTING_PREFIX):]
        if _vertex_model_provider is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "vertex_not_configured",
                    "message": (
                        "Vertex AI is not wired on this container. Ensure VERTEX_PROJECT "
                        "is set and the runtime service account has roles/aiplatform.user, "
                        "then restart. See terraform/main.tf for the IAM binding."
                    ),
                },
            )
        # Return a pre-bound Model instance — Runner uses it without any further
        # provider routing, so the `vertex:` slug never leaks to another client.
        model_instance = _vertex_model_provider.get_model(clean)
        return _vertex_model_provider, model_instance
    return _council_model_provider or MultiProvider(), effective if isinstance(effective, str) else None


async def run_agent_raw(
    agent: Agent,
    prompt: str,
    *,
    model: str | None = None,
    context: Any = None,
) -> Any:
    """Run an agent and return `final_output`.

    Routes between Vertex (in-house) and OpenRouter (GPT-5 only) based on the
    slug's `vertex:` prefix, then wraps the Runner in a retry loop for
    transient provider errors — Cloudflare 524 origin timeouts, empty-choices
    responses, 5xx upstream failures, and rate-limit backpressure.
    """
    provider, clean_model = _resolve_runtime_model(agent, model)

    # IMPORTANT: openai-agents v0.14's Runner ignores RunConfig.model_provider /
    # RunConfig.model when the Agent's own `.model` field is a string — it falls
    # back to the global `set_default_openai_client` (OpenRouter) and sends the
    # raw string slug, leaking our `vertex:…` prefix to OpenRouter which 400s.
    # Cloning the agent with the pre-bound Model instance forces Runner's
    # `get_model()` down the `isinstance(agent.model, Model)` branch — no
    # string, no provider lookup, no fallback to the global OpenRouter client.
    # Falls back to the original agent if clone fails so we never crash on this
    # transformation alone.
    run_agent_obj = agent
    if clean_model is not None and not isinstance(clean_model, str):
        try:
            run_agent_obj = agent.clone(model=clean_model)
        except Exception:
            run_agent_obj = agent

    rc = RunConfig(
        model_provider=provider,
        model=clean_model,  # belt-and-braces; the clone above is the real fix
        trace_include_sensitive_data=True,
    )

    max_attempts = 3
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = await Runner.run(run_agent_obj, prompt, run_config=rc, context=context)
            return result.final_output
        except Exception as exc:
            last_exc = exc
            if _is_bad_model_error(exc):
                # Non-retryable: the model id is wrong or the provider doesn't
                # know it. Bail out early so the caller gets a structured 400
                # instead of three pointless retries.
                break
            if not _is_transient_provider_error(exc) or attempt == max_attempts:
                break
            delay = 1.5 * (2 ** (attempt - 1))  # 1.5s → 3s → 6s
            log.warning(
                "Transient provider error on attempt %d/%d (%s); retrying in %.1fs",
                attempt, max_attempts, exc, delay,
            )
            await asyncio.sleep(delay)

    # All retries exhausted (or we bailed early on a non-retryable error).
    assert last_exc is not None
    if _is_bad_model_error(last_exc):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "bad_model",
                "message": (
                    "The selected model was rejected by the provider. Pick a "
                    "different model from the selector and try again."
                ),
                "provider_message": str(last_exc)[:500],
            },
        ) from last_exc
    if _is_transient_provider_error(last_exc):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "provider_unavailable",
                "message": (
                    "The model provider is temporarily unavailable "
                    f"({type(last_exc).__name__}). This usually clears in a few seconds "
                    "— try again, or switch to a different model."
                ),
            },
        ) from last_exc
    raise last_exc


async def run_agent(
    agent: Agent,
    prompt: str,
    *,
    model: str | None = None,
    context: Any = None,
) -> str:
    """Run an agent and return its final output as a string."""
    output = await run_agent_raw(agent, prompt, model=model, context=context)
    if isinstance(output, str):
        return output
    return output.model_dump_json() if hasattr(output, "model_dump_json") else str(output)


def resolve_for_request(
    req: BaseModel,
    user: Any,
    response: Any,
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


def format_intake_questions_for_api(out: Any) -> str:
    """Parse model output into IntakeFollowupOut, then numbered lines for the UI."""
    if isinstance(out, IntakeFollowupOut):
        model = out
    else:
        model = parse_intake_followup_text(out if isinstance(out, str) else str(out))
    return "\n".join(f"{i + 1}. {q.strip()}" for i, q in enumerate(model.questions))


def parse_json(raw: str) -> dict | list:
    """Robustly extract JSON from model output.

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


__all__ = [
    "_DirectOpenAICompatibleProvider",
    "_VERTEX_ROUTING_PREFIX",
    "_truncate",
    "format_intake_questions_for_api",
    "get_vertex_provider",
    "parse_json",
    "resolve_for_request",
    "run_agent",
    "run_agent_raw",
    "set_providers",
    "traced_workflow",
]
