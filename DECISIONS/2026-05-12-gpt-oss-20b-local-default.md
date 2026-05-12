# Add gpt-oss-20b as the local-dev default model

**Date:** 2026-05-12
**Status:** Approved
**Author:** brainstormed via Claude Code

## Problem

The free-tier default model is `gemini-2-5-flash-lite-free`, which routes through Vertex AI. That requires `VERTEX_PROJECT` to be set and Application Default Credentials (ADC) to resolve to a service account with `roles/aiplatform.user`. For local development without GCP set up, every free-tier call returns `provider_unavailable` and the app is effectively unusable until the developer goes through the GCP setup.

We want a free-tier model that works locally on `OPENROUTER_API_KEY` alone, so a developer cloning the repo can run the app end-to-end without touching GCP.

## Solution

Add OpenAI's open-weight `gpt-oss-20b` (routed through OpenRouter) to the model allowlist as a free-tier entry. Auto-detect whether Vertex is configured at module-load time and pick the right default:

- **Vertex configured** (prod, or local dev with GCP set up): `DEFAULT_MODEL_KEY = "gemini-2-5-flash-lite-free"` — unchanged.
- **Vertex not configured** (local dev without GCP): `DEFAULT_MODEL_KEY = "gpt-oss-20b"` — local-friendly.

Detection signal: presence of `VERTEX_PROJECT`, `GCP_PROJECT`, or `GOOGLE_CLOUD_PROJECT` in the environment. These are already the same vars the lifespan startup uses in `apps/api/medai_api/main.py:179-184`, so the detection logic stays consistent.

## Why this approach

Three alternatives were considered:

1. **New env var override** (`DEFAULT_MODEL_KEY_OVERRIDE=gpt-oss-20b` in local `.env`). Rejected — requires manual config in every dev's `.env`, not "just works".
2. **Lifespan-driven default mutation** (call a `set_default_model_key()` from `main.py` lifespan). Rejected — `from ..council_registry import DEFAULT_MODEL_KEY` in `routers/meta.py:18` snapshots the value at module load (before lifespan runs), so mutation wouldn't propagate without refactoring callers to use a getter function.
3. **Module-load auto-detection** (chosen) — `dotenv` loads at the top of `main.py:36-37` before any transitive `council_registry` import, so `os.environ.get("VERTEX_PROJECT")` at module-load sees the right value. No caller changes, no new env vars.

## Code changes

**File:** `apps/api/medai_api/council_registry.py`

### Change 1: Add gpt-oss-20b entry to `MODELS`

Insert before the existing `gpt-5` entry (keeps OpenRouter-routed models grouped):

```python
"gpt-oss-20b": {
    "id": "openai/gpt-oss-20b",
    "label": "GPT-OSS 20B",
    "tier": "free",
    "description": "OpenAI open-weight via OpenRouter · local-dev default",
},
```

### Change 2: Make `DEFAULT_MODEL_KEY` auto-detect

Replace the constant at `council_registry.py:57`:

```python
DEFAULT_MODEL_KEY = "gemini-2-5-flash-lite-free"
```

with:

```python
import os

_VERTEX_CONFIGURED = bool(
    os.environ.get("VERTEX_PROJECT")
    or os.environ.get("GCP_PROJECT")
    or os.environ.get("GOOGLE_CLOUD_PROJECT")
)
DEFAULT_MODEL_KEY = "gemini-2-5-flash-lite-free" if _VERTEX_CONFIGURED else "gpt-oss-20b"
```

`os` is not currently imported at the top of `council_registry.py`, so the import statement needs to be added with the other top-of-file imports.

That is the entire code diff.

## What cascades automatically

No caller changes needed. The following pick up the new state via existing references:

- `MODEL = MODELS[DEFAULT_MODEL_KEY]["id"]` (back-compat alias at `council_registry.py:61`) — resolves to `openai/gpt-oss-20b` in local dev, `vertex:google/gemini-2.5-flash-lite` in prod. Used by `council.py:18` and `output_guardrails.py:40`.
- `resolve_model()` — uses the new default for invalid model keys and for pro→free downgrades.
- `models_for_plan()` — loops over `MODELS`, so gpt-oss-20b appears in the model-selector list automatically. `tier="free"` means `locked=False` for everyone.
- `routers/meta.py:172` — returns the right `default` key to the frontend.
- `agent_runtime.py` — slug `openai/gpt-oss-20b` has no `vertex:` prefix, so it routes through the existing OpenRouter MultiProvider at line 236. Already wired.
- `langfuse_tracing.py` + `traced_workflow` — same trace path as every other OpenRouter model. No tracing changes.

## What this does NOT change

- **Prod behavior:** When `VERTEX_PROJECT` is set, the default key stays `gemini-2-5-flash-lite-free`. Zero prod impact.
- **Pro tier:** Pro users keep access to every existing pro model. `gpt-oss-20b` is additionally available as a free option.
- **UI / frontend:** No changes. The model selector reads from `models_for_plan()` and renders whatever is in `MODELS`.
- **Auth, rate limiting, escalation, persistence:** Untouched.
- **Env vars:** No new env vars. `OPENROUTER_API_KEY` is already required by lifespan startup at `main.py:150-151`.

## Risks & edge cases

- **Partial GCP setup.** If a developer has `VERTEX_PROJECT` set in `.env` but ADC fails at boot (e.g., they never ran `gcloud auth application-default login`), the default stays `gemini-2-5-flash-lite-free` but Vertex calls return `provider_unavailable`. The lifespan warning at `main.py:219-225` already surfaces this. Mitigation: developer either unsets `VERTEX_PROJECT` or fixes ADC. No code-level mitigation needed.
- **OpenRouter slug stability.** If OpenRouter renames `openai/gpt-oss-20b`, free-tier calls in local dev return a `bad_model` 400. The existing handler at `agent_runtime.py:301-312` surfaces a clean structured error; user is told to pick a different model. Slug is stable per OpenRouter's documented naming convention.
- **Test environments.** No existing test pins the default model key (verified by grepping `tests/`). CI runs without `VERTEX_PROJECT` set, so CI's `DEFAULT_MODEL_KEY` will become `gpt-oss-20b`. Acceptable — tests mock the runner, not the registry constant. If a future test needs deterministic default behavior, it can set `VERTEX_PROJECT=test` in its env.

## Verification

After implementation:

1. **Local dev, no GCP:** Unset `VERTEX_PROJECT` (or never set it). Boot the API. Banner shows `✓ Inference  → Vertex AI  (none) + OpenRouter (gpt-5 only)`. Hit a free-tier endpoint without specifying a model — request succeeds, response comes from `openai/gpt-oss-20b`. Langfuse trace appears.
2. **Local dev, GCP configured:** Set `VERTEX_PROJECT=<project-id>` and run `gcloud auth application-default login`. Boot the API. Default behavior is unchanged: free-tier calls route to `vertex:google/gemini-2.5-flash-lite`.
3. **Model selector:** Hit `GET /meta/models` as a free user. Response includes `gpt-oss-20b` with `tier="free"`, `locked=false`. In local dev (no Vertex), `default` field equals `"gpt-oss-20b"`. In prod, `default` field equals `"gemini-2-5-flash-lite-free"`.
4. **Existing tests:** `pytest apps/api/tests/` passes with no changes.

## Out of scope

- Updating `DECISIONS.md` with a record of this model addition. Recommended as a follow-up, but not part of this spec's code change.
- Updating `README.md` model documentation. Recommended follow-up.
- Adding a `gpt-oss-20b` entry to any frontend model-marketing copy. Auto-rendered from the API.
- Running gpt-oss-20b on a local Ollama / LM Studio server. Possible future addition (a `local:` routing prefix analogous to `vertex:`), but not needed for the current goal.
