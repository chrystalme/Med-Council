# gpt-oss-20b Local Default — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `openai/gpt-oss-20b` (OpenRouter) to the free-tier model allowlist and auto-select it as the default when Vertex AI env vars are not configured, so local dev runs end-to-end without GCP setup.

**Architecture:** Two surgical edits to `apps/api/medai_api/council_registry.py` — a new entry in the `MODELS` dict and an env-driven `DEFAULT_MODEL_KEY` computed at module-load time via a small helper function. Detection extracted into `_compute_default_model_key()` so it can be tested without `importlib.reload`.

**Tech Stack:** Python 3.12, FastAPI, pytest/unittest, OpenAI Agents SDK, OpenRouter.

**Spec reference:** `DECISIONS/2026-05-12-gpt-oss-20b-local-default.md`

---

## File Structure

- **Modify:** `apps/api/medai_api/council_registry.py` (one new `MODELS` entry, replace constant with computed default, add `import os`)
- **Create:** `apps/api/tests/test_council_registry.py` (new test module — none exists today)
- **Run:** Existing test suite in `apps/api/tests/` to confirm no regressions

---

### Task 1: Add tests for `gpt-oss-20b` MODELS entry

**Files:**
- Create: `apps/api/tests/test_council_registry.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for council_registry: model allowlist + env-driven default selection.

Pure data — no network, no I/O.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from medai_api.council_registry import (
    DEFAULT_MODEL_KEY,
    MODELS,
    _compute_default_model_key,
    models_for_plan,
    resolve_model,
)


class GptOss20bAllowlistTest(unittest.TestCase):
    def test_entry_exists_with_expected_fields(self) -> None:
        entry = MODELS["gpt-oss-20b"]
        self.assertEqual(entry["id"], "openai/gpt-oss-20b")
        self.assertEqual(entry["tier"], "free")
        self.assertIn("OpenRouter", entry["description"])

    def test_models_for_plan_includes_gpt_oss_unlocked_for_free(self) -> None:
        keys = {m["key"]: m for m in models_for_plan("free")}
        self.assertIn("gpt-oss-20b", keys)
        self.assertFalse(keys["gpt-oss-20b"]["locked"])

    def test_resolve_model_returns_openrouter_slug(self) -> None:
        slug, downgraded = resolve_model("gpt-oss-20b", "free")
        self.assertEqual(slug, "openai/gpt-oss-20b")
        self.assertFalse(downgraded)


class DefaultModelKeyDetectionTest(unittest.TestCase):
    def test_picks_gpt_oss_when_vertex_unset(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            for var in ("VERTEX_PROJECT", "GCP_PROJECT", "GOOGLE_CLOUD_PROJECT"):
                os.environ.pop(var, None)
            self.assertEqual(_compute_default_model_key(), "gpt-oss-20b")

    def test_picks_gemini_when_vertex_project_set(self) -> None:
        with mock.patch.dict(
            os.environ, {"VERTEX_PROJECT": "test-project"}, clear=False
        ):
            self.assertEqual(
                _compute_default_model_key(), "gemini-2-5-flash-lite-free"
            )

    def test_picks_gemini_when_gcp_project_set(self) -> None:
        with mock.patch.dict(
            os.environ, {"GCP_PROJECT": "test-project"}, clear=False
        ):
            for var in ("VERTEX_PROJECT", "GOOGLE_CLOUD_PROJECT"):
                os.environ.pop(var, None)
            self.assertEqual(
                _compute_default_model_key(), "gemini-2-5-flash-lite-free"
            )

    def test_module_level_default_is_a_valid_key(self) -> None:
        self.assertIn(DEFAULT_MODEL_KEY, MODELS)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && .venv/bin/python -m pytest tests/test_council_registry.py -v`
Expected: FAIL — `KeyError: 'gpt-oss-20b'` for the allowlist tests and `ImportError: cannot import name '_compute_default_model_key'` for the detection tests.

---

### Task 2: Add `gpt-oss-20b` to `MODELS` allowlist

**Files:**
- Modify: `apps/api/medai_api/council_registry.py:24-55` (insert new entry before the `gpt-5` entry)

- [ ] **Step 1: Insert the new entry**

In `MODELS`, after the `llama-3-3-70b` entry and before the `gpt-5` entry, add:

```python
    "gpt-oss-20b": {
        "id": "openai/gpt-oss-20b",
        "label": "GPT-OSS 20B",
        "tier": "free",
        "description": "OpenAI open-weight via OpenRouter · local-dev default",
    },
```

- [ ] **Step 2: Run the allowlist tests**

Run: `cd apps/api && .venv/bin/python -m pytest tests/test_council_registry.py::GptOss20bAllowlistTest -v`
Expected: PASS (3 tests). The `DefaultModelKeyDetectionTest` cases still fail with `ImportError` — that's Task 3.

---

### Task 3: Make `DEFAULT_MODEL_KEY` env-driven

**Files:**
- Modify: `apps/api/medai_api/council_registry.py` — add `import os`, extract `_compute_default_model_key()`, replace constant assignment.

- [ ] **Step 1: Add the `os` import**

At the top of `council_registry.py`, after `from __future__ import annotations`, insert:

```python
import os
```

- [ ] **Step 2: Replace the `DEFAULT_MODEL_KEY` constant**

Replace this line at `council_registry.py:57`:

```python
DEFAULT_MODEL_KEY = "gemini-2-5-flash-lite-free"
```

with:

```python
def _compute_default_model_key() -> str:
    """Pick the free-tier default based on whether Vertex AI is configured.

    In local dev without GCP set up, `gemini-2-5-flash-lite-free` would return
    `provider_unavailable` on every call because Vertex needs `VERTEX_PROJECT`
    + ADC. Fall back to `gpt-oss-20b` (OpenRouter) so a dev with only
    `OPENROUTER_API_KEY` can run the app end-to-end.

    Detection mirrors the env-var check in `main.py` lifespan startup.
    """
    if (
        os.environ.get("VERTEX_PROJECT")
        or os.environ.get("GCP_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
    ):
        return "gemini-2-5-flash-lite-free"
    return "gpt-oss-20b"


DEFAULT_MODEL_KEY = _compute_default_model_key()
```

The downstream `MODEL = MODELS[DEFAULT_MODEL_KEY]["id"]` line on the next line stays as-is.

- [ ] **Step 3: Run the detection tests**

Run: `cd apps/api && .venv/bin/python -m pytest tests/test_council_registry.py -v`
Expected: PASS (7 tests total).

---

### Task 4: Run the full API test suite for regression check

- [ ] **Step 1: Run the suite**

Run: `cd apps/api && .venv/bin/python -m pytest tests/ -q`
Expected: All tests pass. No tests pin the previous default key, so there should be no failures attributable to this change. If a test fails for an unrelated reason (network, fixtures), note it and proceed — the change itself doesn't touch any tested code path beyond the registry constant.

- [ ] **Step 2: Smoke-test the registry interactively**

Run:
```bash
cd apps/api && .venv/bin/python -c "
import os
for v in ('VERTEX_PROJECT','GCP_PROJECT','GOOGLE_CLOUD_PROJECT'):
    os.environ.pop(v, None)
import importlib, medai_api.council_registry as r
importlib.reload(r)
print('local default:', r.DEFAULT_MODEL_KEY)
print('slug:', r.MODELS[r.DEFAULT_MODEL_KEY]['id'])
print('free plan models:')
for m in r.models_for_plan('free'):
    print(' ', m['key'], '→', m['id'], 'locked=', m['locked'])
"
```

Expected output:
```
local default: gpt-oss-20b
slug: openai/gpt-oss-20b
free plan models:
  gemini-2-5-flash-lite-free → vertex:google/gemini-2.5-flash-lite locked= False
  gemini-2-5-pro → vertex:google/gemini-2.5-pro locked= True
  claude-opus-4-7 → vertex:anthropic/claude-opus-4-7 locked= True
  llama-3-3-70b → vertex:meta/llama-3.3-70b-instruct-maas locked= True
  gpt-oss-20b → openai/gpt-oss-20b locked= False
  gpt-5 → openai/gpt-5 locked= True
```

The free-tier user sees `gpt-oss-20b` as unlocked alongside the existing free gemini.

---

### Task 5: Commit

- [ ] **Step 1: Stage and commit**

Run from repo root:

```bash
git add apps/api/medai_api/council_registry.py apps/api/tests/test_council_registry.py
git commit -m "$(cat <<'EOF'
feat(api): add gpt-oss-20b as local-dev default model

Adds OpenAI's open-weight gpt-oss-20b (via OpenRouter) to the free-tier
allowlist and auto-picks it as DEFAULT_MODEL_KEY when no Vertex AI env
var is set. Lets local dev run end-to-end on OPENROUTER_API_KEY alone,
without GCP / ADC setup. Prod behavior unchanged when VERTEX_PROJECT
is configured.

Spec: DECISIONS/2026-05-12-gpt-oss-20b-local-default.md
EOF
)"
```

- [ ] **Step 2: Verify**

Run: `git log -1 --stat`
Expected: One commit, two files changed (council_registry.py + test_council_registry.py).

---

## Self-Review

- **Spec coverage:** Two code changes called out in spec ("Add gpt-oss-20b entry" + "Make DEFAULT_MODEL_KEY auto-detect") map to Task 2 and Task 3 respectively. Verification steps in spec map to Task 4 (registry smoke test + suite run). ✓
- **Placeholders:** None.
- **Type consistency:** `_compute_default_model_key()` referenced consistently across Task 1 (test) and Task 3 (impl). `MODELS["gpt-oss-20b"]` shape matches what `ModelEntry` TypedDict expects (id/label/tier/description, all present in the new entry).
- **Test design:** Each test isolates one behavior, uses `mock.patch.dict` to scope env mutations, and avoids `importlib.reload` for the unit tests (the smoke test in Task 4 uses it intentionally for end-to-end verification).
