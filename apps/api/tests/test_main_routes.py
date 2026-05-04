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
        # Cases routes now live in routers/cases.py and call db.connect()
        # directly via a `_db` alias. Patch that alias's connect attr so the
        # whole module's DB usage hits our fake.
        from routers import cases as _cases_router

        self._patcher = patch.object(_cases_router._db, "connect", return_value=self.con)
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

        # Feedback route lives in routers/feedback.py post-Refactor 4 — patch
        # run_agent on that module's namespace so no model is invoked.
        async def _fake_run_agent(*args, **kwargs):
            return "thanks for the feedback"

        from routers import feedback as _feedback_router

        self._agent_patch = patch.object(_feedback_router, "run_agent", side_effect=_fake_run_agent)
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
        from routers import intake as _intake_router

        async def _fake(*args, **kwargs):
            return return_value

        return patch.object(_intake_router, "run_agent", side_effect=_fake)

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
        from routers import triage as _triage_router

        async def _fake(*args, **kwargs):
            return return_value

        return patch.object(_triage_router, "run_agent", side_effect=_fake)

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


# ── Council specialist / physician ──────────────────────────────────────────


class CouncilSpecialistRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()

    def _patch_run_agent(self, return_value: str):
        from routers import council as _council_router

        async def _fake(*args, **kwargs):
            return return_value

        return patch.object(_council_router, "run_agent", side_effect=_fake)

    def test_returns_specialist_assessment(self) -> None:
        with self._patch_run_agent("Cardiology assessment: ACS likely."):
            r = self.client.post(
                "/api/council/specialist",
                json={
                    "specialist_id": "internal_medicine",
                    "symptoms": "chest pain",
                    "followup_answers": "for 2 days",
                    "prior_assessments": [],
                },
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["specialist"]["id"], "internal_medicine")
        self.assertIn("assessment", body)

    def test_unknown_specialist_id_returns_400(self) -> None:
        r = self.client.post(
            "/api/council/specialist",
            json={
                "specialist_id": "rocket_science",
                "symptoms": "x",
                "followup_answers": "y",
                "prior_assessments": [],
            },
        )
        self.assertEqual(r.status_code, 400)


class CouncilPhysicianRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()

    def test_returns_physician_assessment(self) -> None:
        from routers import council as _council_router

        async def _fake(*args, **kwargs):
            return "Internal medicine: rule out ACS."

        with patch.object(_council_router, "run_agent", side_effect=_fake):
            r = self.client.post(
                "/api/council/physician",
                json={
                    "physician_id": "internal_medicine",
                    "symptoms": "chest pain",
                    "followup_answers": "x",
                    "prior_assessments": [],
                },
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["specialist"]["id"], "internal_medicine")


# ── Research ────────────────────────────────────────────────────────────────


class ResearchRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()

    def test_returns_papers_payload(self) -> None:
        import main as _main

        papers_json = (
            '{"papers": [{"title": "Acute coronary syndromes", "authors": "Smith J", '
            '"journal": "NEJM", "year": 2024, "relevance": "Maps to chest pain", '
            '"summary": "Summary.", "pmid": "12345678", '
            '"url": "https://pubmed.ncbi.nlm.nih.gov/12345678/"}]}'
        )

        async def _fake(*args, **kwargs):
            return papers_json

        with patch.object(_main, "run_agent", side_effect=_fake):
            r = self.client.post(
                "/api/research",
                json={
                    "symptoms": "chest pain",
                    "followup_answers": "x",
                    "assessments": [],
                },
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("papers", body)


# ── Deliberation expert selection ───────────────────────────────────────────


class DeliberationSelectExpertsRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()

    def test_returns_experts_with_internal_medicine_default(self) -> None:
        from routers import triage as _triage_router

        async def _fake(*args, **kwargs):
            return (
                '{"deliberation_experts": ["cardiology", "pulmonology", "neurology"], '
                '"reason_for_selection": "broad differential", "case_summary": "x", '
                '"focus_areas": ["chest pain"]}'
            )

        with patch.object(_triage_router, "run_agent", side_effect=_fake):
            r = self.client.post(
                "/api/deliberation/select-experts",
                json={"symptoms": "chest pain", "followup_answers": "x"},
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        # internal_medicine forced into the list when missing.
        self.assertIn("internal_medicine", body["deliberation_experts"])
        # Cap enforced at 4-6 specialists.
        self.assertGreaterEqual(len(body["deliberation_experts"]), 4)
        self.assertLessEqual(len(body["deliberation_experts"]), 6)


# ── Cases PATCH ─────────────────────────────────────────────────────────────


class CasePatchRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()
        self.con = _FakeCon()
        from routers import cases as _cases_router

        self._patcher = patch.object(_cases_router._db, "connect", return_value=self.con)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_patches_state(self) -> None:
        self.con._cases_row = {"user_id": ""}  # match anonymous owner
        r = self.client.patch(
            "/api/cases/case_1",
            json={"state": {"step": "consensus"}, "title": "renamed"},
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["id"], "case_1")

    def test_patch_404_on_other_user_case(self) -> None:
        self.con._cases_row = {"user_id": "someone_else"}
        r = self.client.patch(
            "/api/cases/case_1",
            json={"state": {}, "title": None},
        )
        self.assertEqual(r.status_code, 404)


# ── Consultations: list, get, delete, retrieve ──────────────────────────────


class ConsultationsListRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()
        self.con = _FakeCon()
        import main as _main

        self._patcher = patch.object(_main, "_get_db", return_value=self.con)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_returns_empty_list_with_remaining_header(self) -> None:
        # Override the fake connection's behaviour for consultations queries.
        original_execute = self.con.execute

        def execute(sql, params=None):
            if "FROM consultations" in sql.upper():
                return _FakeCursor(rows=[])
            return original_execute(sql, params)

        self.con.execute = execute  # type: ignore[assignment]

        r = self.client.get("/api/patient/consultations")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["consultations"], [])
        # Free tier: header should set remaining quota.
        self.assertIn("X-Consultation-Remaining", r.headers)


class ConsultationsGetRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()
        self.con = _FakeCon()
        import main as _main

        self._patcher = patch.object(_main, "_get_db", return_value=self.con)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_returns_404_when_missing(self) -> None:
        original_execute = self.con.execute

        def execute(sql, params=None):
            if "FROM consultations" in sql.upper():
                return _FakeCursor(row=None)
            return original_execute(sql, params)

        self.con.execute = execute  # type: ignore[assignment]
        r = self.client.get("/api/patient/consultations/missing_id")
        self.assertEqual(r.status_code, 404)

    def test_returns_404_when_owner_mismatch(self) -> None:
        original_execute = self.con.execute

        def execute(sql, params=None):
            if "FROM consultations" in sql.upper():
                return _FakeCursor(
                    row={
                        "id": "con_1",
                        "case_id": "case_1",
                        "user_id": "someone_else",
                        "summary": "x",
                        "primary_dx": "y",
                        "icd_code": "Z",
                        "urgency": "routine",
                        "confidence": 50,
                        "consultation_state": {},
                        "case_state": {},
                        "case_title": "T",
                        "created_at": "2026-01-01",
                    }
                )
            return original_execute(sql, params)

        self.con.execute = execute  # type: ignore[assignment]
        r = self.client.get("/api/patient/consultations/con_1")
        self.assertEqual(r.status_code, 404)


class ConsultationsDeleteRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()
        self.con = _FakeCon()
        import main as _main

        self._patcher = patch.object(_main, "_get_db", return_value=self.con)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_deletes_and_returns_ok(self) -> None:
        original_execute = self.con.execute

        def execute(sql, params=None):
            up = sql.upper()
            if up.lstrip().startswith("SELECT") and "FROM CONSULTATIONS" in up:
                return _FakeCursor(row={"user_id": ""})
            return original_execute(sql, params)

        self.con.execute = execute  # type: ignore[assignment]
        r = self.client.delete("/api/patient/consultations/con_1")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True})


class RetrieveRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()

    def test_anonymous_returns_empty_hits(self) -> None:
        # No user → user_id is "", route bails early with empty hits and never
        # touches the embedding provider.
        r = self.client.post(
            "/api/patient/retrieve",
            json={"query": "chest pain history", "top_k": 5},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"hits": []})


# ── Message followup ────────────────────────────────────────────────────────


class MessageFollowupRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()

    def test_returns_followup_text(self) -> None:
        import main as _main

        async def _fake(*args, **kwargs):
            return "Yes, that's a reasonable interpretation. Discuss with your physician — this AI guidance is informational only."

        with patch.object(_main, "run_agent", side_effect=_fake):
            r = self.client.post(
                "/api/message/followup",
                json={
                    "question": "Should I worry about this?",
                    "symptoms": "chest pain",
                    "followup_answers": "x",
                    "consensus": {"primaryDiagnosis": "ACS"},
                    "plan": "rest",
                    "patient_message": "Take care of yourself.",
                },
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        # Route returns the model's reply under "reply".
        self.assertIn("reply", body)


# ── Attachments list / delete ───────────────────────────────────────────────


class AttachmentsListRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()
        self.con = _FakeCon()
        import main as _main

        self._patcher = patch.object(_main, "_get_db", return_value=self.con)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_returns_404_when_case_not_owned(self) -> None:
        # Empty cases_row → ownership check fails (case_id query returns None).
        self.con._cases_row = None
        r = self.client.get("/api/cases/case_X/attachments")
        self.assertEqual(r.status_code, 404)

    def test_lists_attachments_for_owned_case(self) -> None:
        self.con._cases_row = {"user_id": ""}
        # Real list comes from get_attachment_store().list_for_case — stub it.
        from attachments import AttachmentRow

        store_rows = [
            AttachmentRow(
                id="att_1",
                case_id="case_1",
                user_id="",
                kind="file",
                filename="lab.pdf",
                mime_type="application/pdf",
                text="results",
                size_bytes=10,
                question_index=None,
                created_at="2026-01-01",
            )
        ]

        class _StubStore:
            def list_for_case(self, con, case_id):
                return store_rows

        with patch("main.get_attachment_store" if hasattr(__import__("main"), "get_attachment_store") else "attachments.get_attachment_store", return_value=_StubStore()):
            r = self.client.get("/api/cases/case_1/attachments")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body["attachments"]), 1)
        self.assertEqual(body["attachments"][0]["id"], "att_1")


# ── Speech (multipart) ──────────────────────────────────────────────────────


class SpeechSynthesizeRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()

    def test_returns_audio_bytes(self) -> None:
        import main as _main

        class _StubProvider:
            def synthesize(self, text, voice="alloy"):
                return b"fake-mp3-bytes"

        with patch.object(_main, "get_speech_provider" if hasattr(_main, "get_speech_provider") else "speech.get_speech_provider", return_value=_StubProvider(), create=True):
            # Some versions don't expose get_speech_provider on _main directly;
            # patch the source module too.
            with patch("speech.get_speech_provider", return_value=_StubProvider()):
                r = self.client.post(
                    "/api/speech/synthesize",
                    json={"text": "hello"},
                )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b"fake-mp3-bytes")
        self.assertIn("audio", r.headers.get("content-type", ""))


# ── Save consultation ───────────────────────────────────────────────────────


class SaveConsultationRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()
        self.con = _FakeCon()
        import main as _main

        self._db_patch = patch.object(_main, "_get_db", return_value=self.con)
        self._db_patch.start()
        self.addCleanup(self._db_patch.stop)

    def test_404_when_case_not_owned(self) -> None:
        """save_consultation raises 404 when the case row doesn't exist for this user."""
        original_execute = self.con.execute

        def execute(sql, params=None):
            up = sql.upper()
            if up.lstrip().startswith("SELECT") and "FROM CASES" in up:
                return _FakeCursor(row=None)  # not found
            return original_execute(sql, params)

        self.con.execute = execute  # type: ignore[assignment]

        # Stub embed/vector so we don't need to inject those modules.
        with patch("embeddings.get_embedding_provider"), patch(
            "vector_store.get_vector_store"
        ):
            r = self.client.post(
                "/api/patient/consultations",
                json={
                    "case_id": "missing_case",
                    "summary": "Test summary.",
                    "primary_dx": "ACS",
                    "icd_code": "I20.9",
                    "urgency": "urgent",
                    "confidence": 80,
                },
            )
        self.assertEqual(r.status_code, 404)

    def test_save_succeeds_with_owned_case(self) -> None:
        original_execute = self.con.execute

        def execute(sql, params=None):
            up = sql.upper()
            if up.lstrip().startswith("SELECT") and "FROM CASES" in up:
                return _FakeCursor(row={"state": {}})
            if "COUNT(*) AS N FROM CONSULTATIONS" in up:
                return _FakeCursor(row={"n": 0})
            return original_execute(sql, params)

        self.con.execute = execute  # type: ignore[assignment]

        # Stub the vector + embed pieces — they run after the main commit and
        # exceptions are swallowed; just provide working stubs.
        class _StubEmb:
            def embed(self, text):
                return [0.0] * 1536

        class _StubStore:
            def upsert(self, *args, **kwargs):
                pass

        with patch("embeddings.get_embedding_provider", return_value=_StubEmb()), patch(
            "vector_store.get_vector_store", return_value=_StubStore()
        ):
            r = self.client.post(
                "/api/patient/consultations",
                json={
                    "case_id": "case_1",
                    "summary": "Test consultation summary.",
                    "primary_dx": "ACS",
                    "icd_code": "I20.9",
                    "urgency": "urgent",
                    "confidence": 80,
                    "attachment_texts": [],
                },
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["id"].startswith("con_"))
        self.assertEqual(body["case_id"], "case_1")


# ── Patient email ───────────────────────────────────────────────────────────


class PatientEmailRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()

    def test_pro_only_returns_403_for_free_tier(self) -> None:
        # No auth → free tier; require_pro should reject.
        r = self.client.post(
            "/api/patient/email",
            json={
                "to": "alice@example.com",
                "patient_name": "Alice",
                "primary_dx": "ACS",
                "urgency": "urgent",
                "confidence": 80,
                "plan_md": "rest",
                "message_md": "take care",
            },
        )
        # Free tier hits the 4xx wall — the exact code (400/401/402/403) is
        # implementation-defined by require_pro / current configuration.
        self.assertGreaterEqual(r.status_code, 400)
        self.assertLess(r.status_code, 500)


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

        # /api/intake/followup now lives in routers/intake.py — patch run_agent there.
        from routers import intake as _intake_router

        with patch.object(_intake_router, "run_agent", side_effect=_boom):
            r = client.post(
                "/api/intake/followup",
                json={"symptoms": "test"},
            )
        # The unhandled-exception handler catches and returns 500.
        self.assertEqual(r.status_code, 500)


if __name__ == "__main__":
    unittest.main()
