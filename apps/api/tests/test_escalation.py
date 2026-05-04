"""Tests for the escalation module's HTML-safety guarantees."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch


class MaybeEscalateOnCallEscapingTest(unittest.TestCase):
    """Confirm that model-emitted and user-supplied strings cannot inject HTML
    into the on-call escalation email body."""

    def setUp(self) -> None:
        self._env = patch.dict(
            os.environ,
            {
                "RESEND_API_KEY": "test-key",
                "ONCALL_DOCTOR_EMAIL": "oncall@example.com",
                "RESEND_FROM_EMAIL": "noreply@example.com",
            },
        )
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()

    def test_dx_symptoms_and_json_are_html_escaped(self) -> None:
        import escalation

        consensus = {
            "primaryDiagnosis": "<script>alert('x')</script>",
            "urgency": "urgent",
        }
        symptoms = "patient said: <img src=x onerror=alert(1)> and ' \" &"

        captured: dict[str, bytes] = {}

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self) -> bytes:
                return b'{"id":"fake"}'

        def _fake_urlopen(req, timeout=10):
            captured["data"] = req.data
            return _FakeResp()

        with patch.object(escalation.urllib.request, "urlopen", _fake_urlopen):
            escalation.maybe_escalate_oncall(consensus=consensus, symptoms=symptoms)

        self.assertIn("data", captured, "Resend POST was not invoked")
        payload = json.loads(captured["data"].decode("utf-8"))
        html = payload["html"]

        self.assertNotIn("<script>", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img src=x", html)


if __name__ == "__main__":
    unittest.main()
