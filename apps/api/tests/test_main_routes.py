"""Route-level tests for main.py.

Uses FastAPI's TestClient against the real app with the lifespan stubbed
so it never reaches OpenRouter / OpenAI / Vertex / Postgres. Heavy
agent stages are out of scope here — those are exercised in
test_output_guardrails.py via the run_agent_raw patch point.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch


# Env must be set BEFORE importing main — its module-level constants and
# lifespan check look these up directly. The local apps/api/.env normally
# carries a CLERK_ISSUER which would force every test request to 401; clear
# it explicitly so the tests run as anonymous (dev-friendly mode).
os.environ.setdefault("OPENROUTER_API_KEY", "test-or")
os.environ.setdefault("OPENAI_API_KEY", "test-oa")
os.environ.setdefault("SKIP_MIGRATIONS", "1")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("FEEDBACK_SECRET", "test-feedback-secret")
os.environ["CLERK_ISSUER"] = ""  # force unconditional override
# Reload auth so the CLERK_ISSUER override is observed.
import importlib

import auth as _auth  # noqa: E402

importlib.reload(_auth)


def _client():
    """Return a TestClient with lifespan stubbed.

    The real lifespan reaches network (OpenRouter handshake, Vertex auth) and
    DB (alembic upgrade). For route-level tests we just need the FastAPI app
    wired without those side effects.
    """
    from fastapi.testclient import TestClient

    import main as _main

    # Replace lifespan with a no-op that swaps in a dummy MultiProvider so
    # any agent_runtime.set_providers call gets sane state — but the routes
    # tested here don't trigger agent calls, so even a None vertex is fine.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _noop(app):
        yield

    _main.app.router.lifespan_context = _noop
    return TestClient(_main.app)


# ── Public meta routes ──────────────────────────────────────────────────────


class HealthRouteTest(unittest.TestCase):
    def test_health_returns_ok(self) -> None:
        client = _client()
        r = client.get("/health")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("status", body)
        self.assertEqual(body["status"], "ok")


class SpecialistsRouteTest(unittest.TestCase):
    def test_returns_specialist_list(self) -> None:
        client = _client()
        r = client.get("/specialists")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("specialists", body)
        ids = [s["id"] for s in body["specialists"]]
        self.assertIn("internal_medicine", ids)


class AgentsRouteTest(unittest.TestCase):
    def test_returns_agent_inventory(self) -> None:
        client = _client()
        r = client.get("/agents")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        # Inventory shape includes a list of agents.
        self.assertTrue(isinstance(body, dict))


class ModelsRouteTest(unittest.TestCase):
    def test_returns_default_and_models(self) -> None:
        client = _client()
        r = client.get("/api/models")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("default", body)
        self.assertIn("models", body)
        self.assertIn("plan", body)


class MeRouteTest(unittest.TestCase):
    def test_anonymous_call_returns_free_plan(self) -> None:
        client = _client()
        r = client.get("/api/me")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("plan", body)
        # No auth → free
        self.assertEqual(body["plan"], "free")


# ── Cases CRUD ──────────────────────────────────────────────────────────────


class _FakeCursor:
    def __init__(self, row=None, rows=None, rowcount: int = 0) -> None:
        self._row = row
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class _FakeCon:
    """Records execute() calls and serves canned rows by SQL keyword."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, object]] = []
        self.commits = 0
        self.closed = False
        # Programmable.
        self._cases_row: dict | None = None
        self._cases_rows: list[dict] = []

    def execute(self, sql: str, params=None) -> _FakeCursor:
        self.executed.append((sql, params))
        s = sql.strip().upper()
        if s.startswith("SELECT") and "FROM CASES" in s:
            if "ORDER BY" in s or s.endswith("FROM CASES WHERE USER_ID = %S"):
                return _FakeCursor(rows=self._cases_rows)
            return _FakeCursor(row=self._cases_row)
        return _FakeCursor()

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed = True


class CasesCrudTest(unittest.TestCase):
    """Cases CRUD against a stubbed connection. Verifies the routes wire up
    correctly — exhaustive SQL behaviour is exercised in test_attachments /
    test_vector_store style elsewhere.
    """

    def setUp(self) -> None:
        self.client = _client()
        self.con = _FakeCon()
        # Patch _get_db to return our fake connection on every call.
        import main as _main

        self._patcher = patch.object(_main, "_get_db", return_value=self.con)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_create_case(self) -> None:
        r = self.client.post("/api/cases", json={"title": "Test case"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("id", body)
        # uuid4-shaped (8-4-4-4-12 hex), 36 chars
        self.assertEqual(len(body["id"]), 36)
        self.assertEqual(body["title"], "Test case")
        self.assertGreaterEqual(self.con.commits, 1)

    def test_list_cases_returns_rows(self) -> None:
        # When unauthenticated, _cases_user_id returns "" — match that on rows.
        self.con._cases_rows = [
            {
                "id": "case_1",
                "user_id": "",
                "title": "T",
                "state": {},
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        ]
        r = self.client.get("/api/cases")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("cases", body)
        self.assertEqual(len(body["cases"]), 1)

    def test_get_case_404_when_missing(self) -> None:
        self.con._cases_row = None
        r = self.client.get("/api/cases/nonexistent")
        self.assertEqual(r.status_code, 404)

    def test_get_case_returns_row(self) -> None:
        self.con._cases_row = {
            "id": "case_1",
            "user_id": "",  # match the anonymous _cases_user_id == ""
            "title": "Test",
            "state": {"step": "intake"},
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        r = self.client.get("/api/cases/case_1")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["id"], "case_1")
        self.assertEqual(body["state"], {"step": "intake"})

    def test_get_case_404_when_owned_by_another_user(self) -> None:
        self.con._cases_row = {
            "id": "case_1",
            "user_id": "different_user",
            "title": "Test",
            "state": {},
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        r = self.client.get("/api/cases/case_1")
        self.assertEqual(r.status_code, 404)


# ── Feedback ────────────────────────────────────────────────────────────────


class FeedbackRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()
        self.con = _FakeCon()
        import main as _main

        self._db_patch = patch.object(_main, "_get_db", return_value=self.con)
        self._db_patch.start()
        self.addCleanup(self._db_patch.stop)

        # Feedback route runs the agent — stub it so no model is invoked.
        async def _fake_run_agent(*args, **kwargs):
            return "thanks for the feedback"

        self._agent_patch = patch.object(_main, "run_agent", side_effect=_fake_run_agent)
        self._agent_patch.start()
        self.addCleanup(self._agent_patch.stop)

    def test_submit_feedback(self) -> None:
        r = self.client.post(
            "/api/feedback",
            json={"rating": "up", "comment": "It works!", "symptoms": "x", "diagnosis": "y"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok"})

    def test_rejects_invalid_rating(self) -> None:
        r = self.client.post("/api/feedback", json={"rating": "five", "comment": "x"})
        self.assertEqual(r.status_code, 422)

    def test_view_feedback_rejects_bad_token(self) -> None:
        r = self.client.get("/feedback/wrong-token", follow_redirects=False)
        # Bad token returns 403 / 404 — anything in the 4xx range
        self.assertGreaterEqual(r.status_code, 400)
        self.assertLess(r.status_code, 500)

    def test_view_feedback_renders_html_with_correct_token(self) -> None:
        token = os.environ["FEEDBACK_SECRET"]
        r = self.client.get(f"/feedback/{token}")
        self.assertEqual(r.status_code, 200)
        # Page should be HTML (the route returns an HTMLResponse).
        self.assertIn("text/html", r.headers.get("content-type", ""))


# ── Agent stage routes (run_agent stubbed) ──────────────────────────────────


class IntakeFollowupRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()

    def _patch_run_agent(self, return_value: str):
        import main as _main

        async def _fake(*args, **kwargs):
            return return_value

        return patch.object(_main, "run_agent", side_effect=_fake)

    def test_returns_numbered_questions(self) -> None:
        with self._patch_run_agent(
            '{"questions": ["When did this start?", "How severe?", "Other symptoms?", "Past history?"]}'
        ):
            r = self.client.post("/api/intake/followup", json={"symptoms": "chest pain"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("questions", body)
        self.assertIn("1.", body["questions"])
        self.assertIn("4.", body["questions"])


class TriageRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()

    def _patch_run_agent(self, return_value: str):
        import main as _main

        async def _fake(*args, **kwargs):
            return return_value

        return patch.object(_main, "run_agent", side_effect=_fake)

    def test_returns_specialist_selection_with_internal_medicine_default(self) -> None:
        with self._patch_run_agent(
            '{"selected_specialists": ["cardiology"], "reasoning": "exertional", "urgency_flag": "urgent"}'
        ):
            r = self.client.post(
                "/api/triage",
                json={"symptoms": "chest pain", "followup_answers": "for 2 days"},
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("internal_medicine", body["selected_specialist_ids"])
        self.assertIn("cardiology", body["selected_specialist_ids"])
        self.assertEqual(body["urgency_flag"], "urgent")

    def test_502_when_model_returns_unparseable_json(self) -> None:
        with self._patch_run_agent("not json at all"):
            r = self.client.post(
                "/api/triage",
                json={"symptoms": "x", "followup_answers": "y"},
            )
        self.assertEqual(r.status_code, 502)


class ConsensusRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()

    def _patch_run_agent(self, return_value: str):
        import main as _main

        async def _fake(*args, **kwargs):
            return return_value

        return patch.object(_main, "run_agent", side_effect=_fake)

    def test_returns_consensus_payload(self) -> None:
        consensus_json = (
            '{"primaryDiagnosis": "ACS", "icdCode": "I20.9", "confidence": 80, '
            '"differentials": ["GERD"], "prognosis": "good", '
            '"keyFindings": "exertional chest pain", "urgency": "urgent"}'
        )
        with self._patch_run_agent(consensus_json):
            r = self.client.post(
                "/api/consensus",
                json={
                    "symptoms": "chest pain",
                    "followup_answers": "x",
                    "assessments": [],
                    "research": [],
                },
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        # Route wraps the parsed payload in a "consensus" key.
        self.assertIn("consensus", body)
        self.assertEqual(body["consensus"]["urgency"], "urgent")


class PlanRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()

    def _patch_run_agent(self, return_value: str):
        import main as _main

        async def _fake(*args, **kwargs):
            return return_value

        return patch.object(_main, "run_agent", side_effect=_fake)

    def test_returns_plan_text(self) -> None:
        plan_md = (
            "## Immediate next steps\n"
            "- Start aspirin 325 mg.\n"
            "## Tests to order\n- 12-lead ECG.\n"
            "## When to seek emergency care\n- Chest pain not relieved.\n"
            "## Follow-up timeline\n- See cardiology in 1 week.\n"
            "## Red-flag symptoms\n- Syncope.\n"
            "## Questions for the doctor\n- Need stress test?\n"
        )
        with self._patch_run_agent(plan_md):
            r = self.client.post(
                "/api/plan",
                json={
                    "symptoms": "chest pain",
                    "followup_answers": "x",
                    "consensus": {"primaryDiagnosis": "ACS", "urgency": "urgent"},
                    "assessments": [],
                },
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("plan", body)


class MessageRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()

    def test_returns_message_text(self) -> None:
        import main as _main

        message_text = (
            "Take care of yourself today and rest. Remember, this AI summary is not a "
            "replacement for advice from a licensed physician — please consult your doctor."
        )

        async def _fake(*args, **kwargs):
            return message_text

        with patch.object(_main, "run_agent", side_effect=_fake):
            r = self.client.post(
                "/api/message",
                json={
                    "symptoms": "chest pain",
                    "consensus": {"primaryDiagnosis": "ACS", "urgency": "urgent"},
                    "plan": "rest, follow up",
                },
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("message", body)
        self.assertEqual(body.get("retried"), False)


# ── Exception handling ──────────────────────────────────────────────────────


class ExceptionHandlersTest(unittest.TestCase):
    """The /api/triage route raises an HTTPException(502) when JSON parsing
    fails — covered above. The unhandled-exception handler is the catch-all
    for unexpected errors. We exercise it by patching run_agent to raise
    a generic Exception."""

    def setUp(self) -> None:
        self.client = _client()

    def test_unexpected_error_surfaces_as_500(self) -> None:
        import main as _main
        from fastapi.testclient import TestClient

        # raise_server_exceptions=False so TestClient honours our exception
        # handler instead of re-raising.
        client = TestClient(_main.app, raise_server_exceptions=False)

        async def _boom(*args, **kwargs):
            raise RuntimeError("simulated provider crash")

        with patch.object(_main, "run_agent", side_effect=_boom):
            r = client.post(
                "/api/intake/followup",
                json={"symptoms": "test"},
            )
        # The unhandled-exception handler catches and returns 500.
        self.assertEqual(r.status_code, 500)


if __name__ == "__main__":
    unittest.main()
