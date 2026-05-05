"""Unit + integration tests for the medical-topic input guardrail.

Mirror of test_output_guardrails.py — same conventions (sys.path bootstrap,
``unittest.TestCase``, classifier monkeypatched via ``main.run_agent_raw``).
No network.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from medai_api import council  # noqa: E402
from medai_api.council_schemas import MedicalTopicCheck  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────


class _StubAgent:
    name = "stub-input-agent"


class _StubCtx:
    """Stand-in for RunContextWrapper — unused by the input guardrail beyond signature."""

    def __init__(self, context: Any = None) -> None:
        self.context = context


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _patch_classifier(response_text: str):
    """Patch main.run_agent_raw so the medical-topic classifier returns ``response_text``."""

    async def _fake(*args, **kwargs):
        return response_text

    from medai_api import main as _main

    return patch.object(_main, "run_agent_raw", side_effect=_fake)


# ─────────────────────────────────────────────────────────────────────────────
#  _parse_medical_check — unit tests on the JSON parser
# ─────────────────────────────────────────────────────────────────────────────


class ParseMedicalCheckTest(unittest.TestCase):
    def test_plain_json_medical_true(self) -> None:
        result = council._parse_medical_check(
            '{"is_medical": true, "reasoning": "headache mention"}'
        )
        self.assertIsInstance(result, MedicalTopicCheck)
        self.assertTrue(result.is_medical)
        self.assertEqual(result.reasoning, "headache mention")

    def test_plain_json_medical_false(self) -> None:
        result = council._parse_medical_check(
            '{"is_medical": false, "reasoning": "asking for cooking recipe"}'
        )
        self.assertFalse(result.is_medical)
        self.assertEqual(result.reasoning, "asking for cooking recipe")

    def test_fenced_json_passes(self) -> None:
        fenced = '```json\n{"is_medical": false, "reasoning": "trivia question"}\n```'
        result = council._parse_medical_check(fenced)
        self.assertFalse(result.is_medical)

    def test_prose_with_embedded_json_extracts(self) -> None:
        text = (
            "Sure — here is my classification:\n"
            '{"is_medical": true, "reasoning": "patient describes chest pain"}\n'
            "End of analysis."
        )
        result = council._parse_medical_check(text)
        self.assertTrue(result.is_medical)
        self.assertIn("chest pain", result.reasoning)

    def test_unparseable_garbage_defaults_to_medical(self) -> None:
        # Don't block real patients on classifier-output bugs — fail open.
        result = council._parse_medical_check("???")
        self.assertTrue(result.is_medical)
        self.assertIn("Could not parse", result.reasoning)

    def test_empty_input_defaults_to_medical(self) -> None:
        result = council._parse_medical_check("")
        self.assertTrue(result.is_medical)

    def test_keyword_fallback_catches_explicit_false(self) -> None:
        # When JSON parsing fails but the prose explicitly says is_medical: false / no.
        text = "After review, is_medical: false because the user wants football scores."
        result = council._parse_medical_check(text)
        self.assertFalse(result.is_medical)


# ─────────────────────────────────────────────────────────────────────────────
#  _check_medical_topic — unit tests on the guardrail function
# ─────────────────────────────────────────────────────────────────────────────


class CheckMedicalTopicTest(unittest.TestCase):
    def test_medical_input_passes(self) -> None:
        with _patch_classifier(
            '{"is_medical": true, "reasoning": "headache, photophobia"}'
        ):
            result = _run(
                council._check_medical_topic(
                    _StubCtx(),
                    _StubAgent(),
                    "I have a severe headache and sensitivity to light",
                )
            )
        self.assertFalse(result.tripwire_triggered)
        self.assertTrue(result.output_info["is_medical"])

    def test_non_medical_input_trips(self) -> None:
        with _patch_classifier(
            '{"is_medical": false, "reasoning": "user is asking for a pasta recipe"}'
        ):
            result = _run(
                council._check_medical_topic(
                    _StubCtx(),
                    _StubAgent(),
                    "How do I make carbonara from scratch?",
                )
            )
        self.assertTrue(result.tripwire_triggered)
        self.assertFalse(result.output_info["is_medical"])
        self.assertIn("pasta recipe", result.output_info["reasoning"])

    def test_classifier_garbage_passes_through_as_medical(self) -> None:
        # Fail-open: don't block legitimate medical input on classifier bugs.
        with _patch_classifier("???"):
            result = _run(
                council._check_medical_topic(
                    _StubCtx(), _StubAgent(), "I have chest pain"
                )
            )
        self.assertFalse(result.tripwire_triggered)

    def test_list_input_is_stringified(self) -> None:
        # The signature accepts ``str | list``; the guardrail must not crash
        # when the SDK passes a list of input items.
        with _patch_classifier(
            '{"is_medical": true, "reasoning": "described symptoms"}'
        ):
            result = _run(
                council._check_medical_topic(
                    _StubCtx(),
                    _StubAgent(),
                    [{"role": "user", "content": "I have a fever"}],
                )
            )
        self.assertFalse(result.tripwire_triggered)


# ─────────────────────────────────────────────────────────────────────────────
#  Integration: /api/intake/followup maps a tripwire to a 200 nudge response
# ─────────────────────────────────────────────────────────────────────────────


class IntakeRouteIntegrationTest(unittest.TestCase):
    """Drive ``/api/intake/followup`` end-to-end with a synthetic tripwire.

    The route catches ``InputGuardrailTripwireTriggered`` directly. We patch
    the router's ``run_agent`` so the intake call raises a constructed
    tripwire, then assert the response surfaces a friendly ``needs_symptoms``
    nudge instead of erroring out — sending a greeting like "Hi" should
    invite the patient to describe symptoms, not return HTTP 422.
    """

    def setUp(self) -> None:
        from agents import GuardrailFunctionOutput, InputGuardrailTripwireTriggered
        from agents.guardrail import InputGuardrailResult

        # Build a synthetic tripwire that mirrors what the real classifier would emit.
        fake_output = GuardrailFunctionOutput(
            output_info={
                "is_medical": False,
                "reasoning": "Cooking recipe request, not a medical question.",
            },
            tripwire_triggered=True,
        )
        self._tripwire = InputGuardrailTripwireTriggered(
            InputGuardrailResult(
                guardrail=council.medical_topic_guardrail,
                output=fake_output,
            )
        )

    def _post_with_tripwire(self, symptoms: str):
        from fastapi.testclient import TestClient

        from medai_api import main as _main
        from medai_api.auth import current_user_maybe_required
        from medai_api.routers import intake as _intake_router

        async def _raise_tripwire(*args, **kwargs):
            raise self._tripwire

        # Bypass Clerk auth in tests (the dev .env may set CLERK_ISSUER, which
        # would otherwise 401 the request before the guardrail path runs).
        _main.app.dependency_overrides[current_user_maybe_required] = lambda: None
        try:
            with patch.object(_intake_router, "run_agent", side_effect=_raise_tripwire):
                client = TestClient(_main.app)
                return client.post("/api/intake/followup", json={"symptoms": symptoms})
        finally:
            _main.app.dependency_overrides.pop(current_user_maybe_required, None)

    def test_greeting_returns_friendly_nudge(self) -> None:
        resp = self._post_with_tripwire("Hi")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["questions"], [])
        self.assertTrue(body["needs_symptoms"])
        # Friendly framing for the patient who just said hello.
        self.assertIn("Hi!", body["message"])
        self.assertIn("symptom", body["message"].lower())

    def test_off_topic_input_returns_firm_message(self) -> None:
        # A substantive off-topic prompt (cooking recipe, jailbreak attempt,
        # anything actively leading the LLM elsewhere) gets the firm message.
        resp = self._post_with_tripwire("How do I cook the perfect carbonara with guanciale?")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["questions"], [])
        self.assertTrue(body["needs_symptoms"])
        self.assertIn("medical questions only", body["message"])
        self.assertIn("Cooking recipe", body["reasoning"])


if __name__ == "__main__":
    unittest.main()
