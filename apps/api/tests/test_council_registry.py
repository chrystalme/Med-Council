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
        self.assertEqual(entry["id"], "openai/gpt-oss-20b:free")
        self.assertEqual(entry["tier"], "free")
        self.assertIn("OpenRouter", entry["description"])

    def test_models_for_plan_includes_gpt_oss_unlocked_for_free(self) -> None:
        keys = {m["key"]: m for m in models_for_plan("free")}
        self.assertIn("gpt-oss-20b", keys)
        self.assertFalse(keys["gpt-oss-20b"]["locked"])

    def test_resolve_model_returns_openrouter_slug(self) -> None:
        slug, downgraded = resolve_model("gpt-oss-20b", "free")
        self.assertEqual(slug, "openai/gpt-oss-20b:free")
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
