import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from consultation_memory import MAX_MEMORY_DOCUMENT_CHARS, build_consultation_memory_text


class ConsultationMemoryTest(unittest.TestCase):
    def test_build_consultation_memory_text_includes_full_session_sections(self) -> None:
        text = build_consultation_memory_text(
            summary="Key findings only",
            primary_dx="Migraine",
            icd_code="G43.909",
            urgency="routine",
            confidence=82,
            attachment_texts=["MRI report: no acute findings"],
            case_state={
                "symptoms": "Severe headache with photophobia",
                "councilRoster": [
                    {"name": "Dr Primary", "specialty": "Internal Medicine"}
                ],
                "deliberationCaseSummary": "Headache case summary",
                "deliberationFocusAreas": ["neurologic red flags"],
                "deliberationReason": "Neurology input is needed",
                "fqLines": ["When did it start?"],
                "fqAnswers": ["Yesterday evening"],
                "physicians": [
                    {
                        "name": "Dr Neuro",
                        "specialty": "Neurology",
                        "assessment": "Likely migraine without red flags.",
                    }
                ],
                "plan": "Hydration, NSAID trial, follow up if worsening.",
                "message": "Your symptoms are most consistent with a migraine.",
            },
        )

        self.assertIn("Key findings only", text)
        self.assertIn("Severe headache with photophobia", text)
        self.assertIn("Internal Medicine - Dr Primary", text)
        self.assertIn("Headache case summary", text)
        self.assertIn("neurologic red flags", text)
        self.assertIn("Neurology input is needed", text)
        self.assertIn("Q: When did it start?", text)
        self.assertIn("A: Yesterday evening", text)
        self.assertIn("Likely migraine without red flags.", text)
        self.assertIn("Hydration, NSAID trial", text)
        self.assertIn("most consistent with a migraine", text)
        self.assertIn("MRI report: no acute findings", text)

    def test_build_consultation_memory_text_caps_output_size(self) -> None:
        text = build_consultation_memory_text(
            summary="x" * (MAX_MEMORY_DOCUMENT_CHARS * 2),
            attachment_texts=["y" * (MAX_MEMORY_DOCUMENT_CHARS * 2)],
            case_state={
                "symptoms": "z" * (MAX_MEMORY_DOCUMENT_CHARS * 2),
            },
        )

        self.assertLessEqual(len(text), MAX_MEMORY_DOCUMENT_CHARS + len("\n[truncated]"))
        self.assertIn("[truncated]", text)
