"""Tests for council_schemas: Pydantic validators and the parse_* functions
that convert model output into structured shapes.

Pure data — no network, no I/O.
"""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from council_schemas import (
    ConsensusOut,
    IntakeFollowupOut,
    MedicalTopicCheck,
    PatientSymptomsIn,
    parse_intake_followup_text,
    parse_research_papers,
)


# ── Pydantic models ────────────────────────────────────────────────────────


class MedicalTopicCheckTest(unittest.TestCase):
    def test_round_trip(self) -> None:
        m = MedicalTopicCheck(is_medical=True, reasoning="symptoms described")
        re_parsed = MedicalTopicCheck.model_validate_json(m.model_dump_json())
        self.assertEqual(re_parsed, m)


class PatientSymptomsInTest(unittest.TestCase):
    def test_strips_whitespace(self) -> None:
        p = PatientSymptomsIn(symptoms="  chest pain  ")
        self.assertEqual(p.symptoms, "chest pain")

    def test_rejects_empty_after_strip(self) -> None:
        with self.assertRaises(ValidationError):
            PatientSymptomsIn(symptoms="   ")

    def test_model_field_optional(self) -> None:
        p = PatientSymptomsIn(symptoms="x", model=None)
        self.assertIsNone(p.model)


class IntakeFollowupOutTest(unittest.TestCase):
    QS = [
        "When did the symptoms begin?",
        "How would you describe the severity?",
        "Any associated symptoms?",
        "Any relevant medical history?",
    ]

    def test_round_trip(self) -> None:
        out = IntakeFollowupOut(questions=list(self.QS))
        self.assertEqual(out.questions, self.QS)

    def test_strips_numeric_prefix(self) -> None:
        out = IntakeFollowupOut(
            questions=[
                "1. When did the symptoms begin?",
                "2) How severe?",
                "- Any associated symptoms?",
                "• Any history?",
            ]
        )
        self.assertEqual(
            out.questions,
            [
                "When did the symptoms begin?",
                "How severe?",
                "Any associated symptoms?",
                "Any history?",
            ],
        )

    def test_rejects_three_questions(self) -> None:
        with self.assertRaises(ValidationError):
            IntakeFollowupOut(questions=self.QS[:3])

    def test_rejects_five_questions(self) -> None:
        with self.assertRaises(ValidationError):
            IntakeFollowupOut(questions=[*self.QS, "extra?"])

    def test_rejects_extra_keys(self) -> None:
        with self.assertRaises(ValidationError):
            IntakeFollowupOut.model_validate({"questions": list(self.QS), "extra": True})


class ConsensusOutTest(unittest.TestCase):
    BASE = {
        "primaryDiagnosis": "Acute coronary syndrome",
        "icdCode": "I20.9",
        "confidence": 80,
        "differentials": ["GERD", "anxiety"],
        "prognosis": "Good with prompt care.",
        "keyFindings": "Exertional chest pain.",
        "urgency": "urgent",
    }

    def test_round_trip(self) -> None:
        c = ConsensusOut(**self.BASE)
        self.assertEqual(c.confidence, 80)
        self.assertEqual(c.urgency, "urgent")

    def test_confidence_bounds(self) -> None:
        with self.assertRaises(ValidationError):
            ConsensusOut(**{**self.BASE, "confidence": -1})
        with self.assertRaises(ValidationError):
            ConsensusOut(**{**self.BASE, "confidence": 101})

    def test_urgency_must_be_canonical(self) -> None:
        with self.assertRaises(ValidationError):
            ConsensusOut(**{**self.BASE, "urgency": "stat"})

    def test_extra_fields_allowed(self) -> None:
        c = ConsensusOut(**{**self.BASE, "futureField": "ride it out"})
        self.assertEqual(c.urgency, "urgent")

    def test_icd_can_be_empty(self) -> None:
        c = ConsensusOut(**{**self.BASE, "icdCode": ""})
        self.assertEqual(c.icdCode, "")


# ── parse_intake_followup_text ──────────────────────────────────────────────


class ParseIntakeFollowupTest(unittest.TestCase):
    QS = [
        "When did the symptoms begin?",
        "How would you describe the severity?",
        "Any associated symptoms?",
        "Any relevant medical history?",
    ]

    def test_strict_json_with_questions_key(self) -> None:
        text = '{"questions": ' + str(self.QS).replace("'", '"') + "}"
        out = parse_intake_followup_text(text)
        self.assertEqual(out.questions, self.QS)

    def test_fenced_json(self) -> None:
        text = "```json\n{\"questions\": " + str(self.QS).replace("'", '"') + "}\n```"
        out = parse_intake_followup_text(text)
        self.assertEqual(out.questions, self.QS)

    def test_prose_fallback_with_numbered_lines(self) -> None:
        text = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(self.QS))
        out = parse_intake_followup_text(text)
        self.assertEqual(out.questions, self.QS)

    def test_prose_fallback_with_blank_separated_paragraphs(self) -> None:
        text = "\n\n".join(self.QS)
        out = parse_intake_followup_text(text)
        self.assertEqual(out.questions, self.QS)

    def test_empty_input_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_intake_followup_text("")

    def test_unparseable_input_raises(self) -> None:
        # Three lines — not enough for the four-question contract.
        with self.assertRaises(ValueError):
            parse_intake_followup_text("a?\nb?\nc?")


# ── parse_research_papers ────────────────────────────────────────────────────


class ParseResearchPapersTest(unittest.TestCase):
    def test_normal_json_with_papers_key(self) -> None:
        text = """{
            "papers": [
                {
                    "title": "Acute coronary syndromes review",
                    "authors": "Smith J, Doe K",
                    "journal": "NEJM",
                    "year": 2024,
                    "relevance": "Maps to chest pain triage.",
                    "summary": "Summary.",
                    "pmid": "12345678",
                    "url": "https://pubmed.ncbi.nlm.nih.gov/12345678/"
                }
            ]
        }"""
        papers, err = parse_research_papers(text)
        self.assertIsNone(err)
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["pmid"], "12345678")
        self.assertEqual(papers[0]["year"], 2024)

    def test_alt_keys_normalised(self) -> None:
        # Tests _normalize_paper_dict's handling of varied key names.
        text = """{
            "references": [
                {
                    "Title": "Chest pain triage",
                    "PMID": "9876543",
                    "Authors": "Lee X",
                    "Journal": "Lancet",
                    "Year": "2020",
                    "Abstract": "Background and methods…"
                }
            ]
        }"""
        papers, err = parse_research_papers(text)
        self.assertIsNone(err)
        self.assertEqual(papers[0]["title"], "Chest pain triage")
        self.assertEqual(papers[0]["pmid"], "9876543")
        self.assertEqual(papers[0]["year"], 2020)
        self.assertIn("pubmed.ncbi.nlm.nih.gov/9876543", papers[0]["url"])

    def test_pmid_recovery_from_prose(self) -> None:
        text = "Reviewed PMID: 11223344 and the chest pain literature broadly."
        papers, err = parse_research_papers(text)
        self.assertIsNotNone(err)
        self.assertEqual(papers[0]["pmid"], "11223344")
        self.assertIn("11223344", papers[0]["url"])

    def test_unstructured_prose_returns_fallback_card(self) -> None:
        text = "I think you should consider acute coronary syndrome and GERD."
        papers, err = parse_research_papers(text)
        # No PMIDs, no JSON — single fallback narrative card returned.
        self.assertEqual(len(papers), 1)
        self.assertIn("Literature review", papers[0]["title"])

    def test_empty_input_returns_error(self) -> None:
        papers, err = parse_research_papers("")
        self.assertEqual(papers, [])
        self.assertIsNotNone(err)

    def test_doi_only_paper_synthesises_url(self) -> None:
        text = """{"papers": [{"title": "X", "doi": "10.1056/NEJM.example"}]}"""
        papers, _ = parse_research_papers(text)
        self.assertEqual(papers[0]["url"], "https://doi.org/10.1056/NEJM.example")

    def test_caps_at_eight_papers(self) -> None:
        items = [{"title": f"paper {i}", "pmid": str(10000000 + i)} for i in range(12)]
        text = '{"papers": ' + str(items).replace("'", '"') + "}"
        papers, _ = parse_research_papers(text)
        self.assertEqual(len(papers), 8)


if __name__ == "__main__":
    unittest.main()
