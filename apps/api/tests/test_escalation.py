"""Tests for the escalation module.

Every Resend-bound code path is exercised against a stubbed urlopen — no
network is hit. Test coverage focus: HTML safety, configuration gating,
urgency detection, recipient resolution, and the Resend transport's
success / 4xx / transport-error branches.
"""

from __future__ import annotations

import json
import os
import unittest
import urllib.error
from io import BytesIO
from unittest.mock import patch


# ── Tiny test doubles ───────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, body: bytes = b'{"id":"fake"}') -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


def _capturing_urlopen():
    """Returns (fake_urlopen, captured) — fake_urlopen records the request."""
    captured: dict[str, bytes] = {}

    def _fake(req, timeout=None):
        captured["url"] = getattr(req, "full_url", "").encode()
        captured["data"] = req.data
        return _FakeResp()

    return _fake, captured


# ── Pure helpers ────────────────────────────────────────────────────────────


class IsUrgentTest(unittest.TestCase):
    def test_canonical_urgency_strings(self) -> None:
        from medai_api.escalation import is_urgent

        for value in ("urgent", "EMERGENT", " emergency ", "Stat", "immediate", "critical"):
            with self.subTest(value=value):
                self.assertTrue(is_urgent({"urgency": value}))

    def test_routine_and_unknown_are_not_urgent(self) -> None:
        from medai_api.escalation import is_urgent

        self.assertFalse(is_urgent({"urgency": "routine"}))
        self.assertFalse(is_urgent({"urgency": "low"}))
        self.assertFalse(is_urgent({}))

    def test_alt_camelcase_key(self) -> None:
        from medai_api.escalation import is_urgent

        self.assertTrue(is_urgent({"urgencyLevel": "URGENT"}))


class SafeSubjectPartTest(unittest.TestCase):
    def test_strips_control_chars_and_caps_length(self) -> None:
        from medai_api.escalation import _safe_subject_part

        # Embedded CR/LF and control bytes — must all be filtered.
        out = _safe_subject_part("subject\r\nx\x00y\tz", max_len=120)
        self.assertNotIn("\r", out)
        self.assertNotIn("\n", out)
        self.assertNotIn("\x00", out)

        # max_len caps the result.
        capped = _safe_subject_part("a" * 500, max_len=10)
        self.assertEqual(len(capped), 10)


class MaskEmailTest(unittest.TestCase):
    def test_local_part_is_masked(self) -> None:
        from medai_api.escalation import _mask_email

        self.assertEqual(_mask_email("alice@clinic.com"), "ali***@clinic.com")

    def test_no_at_sign_returns_stars(self) -> None:
        from medai_api.escalation import _mask_email

        self.assertEqual(_mask_email("nothing-special"), "***")
        self.assertEqual(_mask_email(""), "***")


class ResolveRecipientTest(unittest.TestCase):
    def test_passthrough_when_no_override(self) -> None:
        from medai_api.escalation import _resolve_recipient

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EMAIL_OVERRIDE_TO", None)
            self.assertEqual(_resolve_recipient("alice@clinic.com"), "alice@clinic.com")

    def test_override_redirects_when_different(self) -> None:
        from medai_api.escalation import _resolve_recipient

        with patch.dict(os.environ, {"EMAIL_OVERRIDE_TO": "dev@example.com"}):
            self.assertEqual(_resolve_recipient("alice@clinic.com"), "dev@example.com")

    def test_override_passthrough_when_match(self) -> None:
        from medai_api.escalation import _resolve_recipient

        with patch.dict(os.environ, {"EMAIL_OVERRIDE_TO": "alice@clinic.com"}):
            # Already pointed at the target — no redirect, just return as-is.
            self.assertEqual(_resolve_recipient("alice@clinic.com"), "alice@clinic.com")


# ── maybe_escalate_oncall ────────────────────────────────────────────────────


class MaybeEscalateOnCallTest(unittest.TestCase):
    """The on-call page route. Covers config gating, urgency gating, HTML
    escape (the C2 fix) and Resend HTTP-error swallowing."""

    def _enable_resend(self) -> patch:
        return patch.dict(
            os.environ,
            {
                "RESEND_API_KEY": "test-key",
                "ONCALL_DOCTOR_EMAIL": "oncall@example.com",
                "RESEND_FROM_EMAIL": "noreply@example.com",
            },
        )

    def test_skipped_when_resend_not_configured(self) -> None:
        from medai_api import escalation

        # No RESEND_API_KEY env at all — the function should bail before any
        # urlopen call. We assert urlopen is never invoked.
        called: list[bool] = []

        def _spy(*args, **kwargs):
            called.append(True)
            return _FakeResp()

        with patch.dict(os.environ, {"RESEND_API_KEY": "", "ONCALL_DOCTOR_EMAIL": "", "RESEND_FROM_EMAIL": ""}):
            with patch.object(escalation.urllib.request, "urlopen", _spy):
                escalation.maybe_escalate_oncall(consensus={"urgency": "urgent"}, symptoms="x")
        self.assertEqual(called, [], "urlopen must not be invoked when Resend isn't configured")

    def test_skipped_when_urgency_not_in_allowlist(self) -> None:
        from medai_api import escalation

        called: list[bool] = []

        def _spy(*args, **kwargs):
            called.append(True)
            return _FakeResp()

        with self._enable_resend(), patch.object(escalation.urllib.request, "urlopen", _spy):
            escalation.maybe_escalate_oncall(consensus={"urgency": "routine"}, symptoms="x")
        self.assertEqual(called, [], "urlopen must not be invoked when urgency is routine")

    def test_sends_when_configured_and_urgent(self) -> None:
        from medai_api import escalation

        fake_urlopen, captured = _capturing_urlopen()
        with self._enable_resend(), patch.object(escalation.urllib.request, "urlopen", fake_urlopen):
            escalation.maybe_escalate_oncall(consensus={"urgency": "urgent", "primaryDiagnosis": "MI"}, symptoms="chest pain")
        self.assertIn("data", captured)
        payload = json.loads(captured["data"].decode("utf-8"))
        self.assertEqual(payload["from"], "noreply@example.com")
        self.assertIn("MI", payload["html"])

    def test_dx_symptoms_and_json_are_html_escaped(self) -> None:
        """Regression test for the C2 fix — model-emitted tags must not leak."""
        from medai_api import escalation

        consensus = {
            "primaryDiagnosis": "<script>alert('x')</script>",
            "urgency": "urgent",
        }
        symptoms = "patient said: <img src=x onerror=alert(1)>"

        fake_urlopen, captured = _capturing_urlopen()
        with self._enable_resend(), patch.object(escalation.urllib.request, "urlopen", fake_urlopen):
            escalation.maybe_escalate_oncall(consensus=consensus, symptoms=symptoms)

        payload = json.loads(captured["data"].decode("utf-8"))
        html = payload["html"]
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;", html)

    def test_http_error_is_swallowed(self) -> None:
        """Resend 4xx must not raise — escalation is best-effort."""
        from medai_api import escalation

        def _raise(*args, **kwargs):
            raise urllib.error.HTTPError(
                url="https://api.resend.com/emails",
                code=429,
                msg="Too Many Requests",
                hdrs=None,  # type: ignore[arg-type]
                fp=BytesIO(b'{"error":"rate_limited"}'),
            )

        with self._enable_resend(), patch.object(escalation.urllib.request, "urlopen", _raise):
            # Must not raise.
            escalation.maybe_escalate_oncall(consensus={"urgency": "urgent"}, symptoms="x")


# ── notify_doctor_with_message ──────────────────────────────────────────────


class NotifyDoctorWithMessageTest(unittest.TestCase):
    def _enable_resend(self) -> patch:
        return patch.dict(
            os.environ,
            {"RESEND_API_KEY": "test-key", "RESEND_FROM_EMAIL": "noreply@example.com"},
        )

    def test_skipped_when_recipient_blank(self) -> None:
        from medai_api import escalation

        with self._enable_resend():
            self.assertEqual(
                escalation.notify_doctor_with_message(
                    doctor_email="",
                    consensus={"urgency": "urgent"},
                    plan_md="",
                    patient_message="",
                    symptoms="",
                ),
                "skipped",
            )

    def test_skipped_when_resend_not_configured(self) -> None:
        from medai_api import escalation

        with patch.dict(os.environ, {"RESEND_API_KEY": "", "RESEND_FROM_EMAIL": ""}):
            self.assertEqual(
                escalation.notify_doctor_with_message(
                    doctor_email="alice@clinic.com",
                    consensus={"urgency": "urgent"},
                    plan_md="",
                    patient_message="",
                    symptoms="",
                ),
                "skipped",
            )

    def test_skipped_when_not_urgent(self) -> None:
        from medai_api import escalation

        with self._enable_resend():
            self.assertEqual(
                escalation.notify_doctor_with_message(
                    doctor_email="alice@clinic.com",
                    consensus={"urgency": "routine"},
                    plan_md="",
                    patient_message="",
                    symptoms="",
                ),
                "skipped",
            )

    def test_sent_when_all_preconditions_met(self) -> None:
        from medai_api import escalation

        fake_urlopen, captured = _capturing_urlopen()
        with self._enable_resend(), patch.object(escalation.urllib.request, "urlopen", fake_urlopen):
            status = escalation.notify_doctor_with_message(
                doctor_email="alice@clinic.com",
                consensus={"urgency": "urgent", "primaryDiagnosis": "PE", "icdCode": "I26.9", "confidence": 87},
                plan_md="**Order** D-dimer.",
                patient_message="See your doctor.",
                symptoms="dyspnoea",
            )
        self.assertEqual(status, "sent")
        payload = json.loads(captured["data"].decode("utf-8"))
        self.assertIn("PE", payload["subject"])
        self.assertIn("URGENT", payload["subject"])
        self.assertIn("dyspnoea", payload["html"])

    def test_returns_failed_on_http_error(self) -> None:
        from medai_api import escalation

        def _raise(*args, **kwargs):
            raise urllib.error.HTTPError(
                url="https://api.resend.com/emails",
                code=400,
                msg="Bad Request",
                hdrs=None,  # type: ignore[arg-type]
                fp=BytesIO(b'{"error":"invalid"}'),
            )

        with self._enable_resend(), patch.object(escalation.urllib.request, "urlopen", _raise):
            self.assertEqual(
                escalation.notify_doctor_with_message(
                    doctor_email="alice@clinic.com",
                    consensus={"urgency": "urgent"},
                    plan_md="",
                    patient_message="",
                    symptoms="",
                ),
                "failed",
            )


# ── send_patient_email ──────────────────────────────────────────────────────


class SendPatientEmailTest(unittest.TestCase):
    def test_raises_when_not_configured(self) -> None:
        from medai_api import escalation

        with patch.dict(os.environ, {"RESEND_API_KEY": "", "RESEND_FROM_EMAIL": ""}):
            with self.assertRaises(escalation.ResendNotConfiguredError):
                escalation.send_patient_email(to="alice@example.com")

    def test_default_subject_when_no_dx(self) -> None:
        from medai_api import escalation

        fake_urlopen, captured = _capturing_urlopen()
        with patch.dict(
            os.environ,
            {"RESEND_API_KEY": "test", "RESEND_FROM_EMAIL": "noreply@example.com"},
        ), patch.object(escalation.urllib.request, "urlopen", fake_urlopen):
            result = escalation.send_patient_email(to="alice@example.com", message_md="hello")

        self.assertIsInstance(result, dict)
        payload = json.loads(captured["data"].decode("utf-8"))
        self.assertEqual(payload["subject"], "Your MedAI Council consultation summary")

    def test_subject_includes_primary_dx(self) -> None:
        from medai_api import escalation

        fake_urlopen, captured = _capturing_urlopen()
        with patch.dict(
            os.environ,
            {"RESEND_API_KEY": "test", "RESEND_FROM_EMAIL": "noreply@example.com"},
        ), patch.object(escalation.urllib.request, "urlopen", fake_urlopen):
            escalation.send_patient_email(to="alice@example.com", primary_dx="MI", message_md="hi")

        payload = json.loads(captured["data"].decode("utf-8"))
        self.assertIn("MI", payload["subject"])

    def test_reply_to_threaded_when_supplied(self) -> None:
        from medai_api import escalation

        fake_urlopen, captured = _capturing_urlopen()
        with patch.dict(
            os.environ,
            {"RESEND_API_KEY": "test", "RESEND_FROM_EMAIL": "noreply@example.com"},
        ), patch.object(escalation.urllib.request, "urlopen", fake_urlopen):
            escalation.send_patient_email(
                to="alice@example.com",
                message_md="hi",
                reply_to="doc@example.com",
            )

        payload = json.loads(captured["data"].decode("utf-8"))
        self.assertEqual(payload.get("reply_to"), ["doc@example.com"])

    def test_http_error_raises_runtime_error(self) -> None:
        from medai_api import escalation

        def _raise(*args, **kwargs):
            raise urllib.error.HTTPError(
                url="https://api.resend.com/emails",
                code=422,
                msg="Unprocessable Entity",
                hdrs=None,  # type: ignore[arg-type]
                fp=BytesIO(b'{"error":"bad-from"}'),
            )

        with patch.dict(
            os.environ,
            {"RESEND_API_KEY": "test", "RESEND_FROM_EMAIL": "noreply@example.com"},
        ), patch.object(escalation.urllib.request, "urlopen", _raise):
            with self.assertRaises(RuntimeError):
                escalation.send_patient_email(to="alice@example.com", message_md="hi")


if __name__ == "__main__":
    unittest.main()
