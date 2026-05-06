"""
Pydantic models for structured agent outputs (and shared API shapes).

Some agents use SDK `output_type=...`; intake uses plain text plus
`parse_intake_followup_text` because OpenRouter models often return prose, not JSON.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator


class MedicalTopicCheck(BaseModel):
    """Output of the medical topic guardrail classifier."""

    is_medical: bool = Field(description="True if the input describes a medical/health concern.")
    reasoning: str = Field(description="Brief explanation of the classification.")


class PatientSymptomsIn(BaseModel):
    """Structured intake request body (mirrors API; optional use in docs/tests)."""

    symptoms: Annotated[str, Field(min_length=1, description="Patient's self-reported symptoms.")]
    model: str | None = Field(
        default=None,
        description="Optional allowlist key from council_registry.MODELS. Free users picking a Pro model are silently downgraded.",
    )

    @field_validator("symptoms", mode="before")
    @classmethod
    def strip_ws(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v


class IntakeFollowupOut(BaseModel):
    """Exactly four plain-text follow-up questions; numbering is applied when rendering for the UI."""

    model_config = {"extra": "forbid"}

    questions: Annotated[
        list[str],
        Field(
            min_length=4,
            max_length=4,
            description=(
                "Four distinct clinical questions: onset/duration, severity/character, "
                "associated symptoms, relevant history. Plain sentences only — do not prefix with numbers."
            ),
        ),
    ]

    @field_validator("questions", mode="before")
    @classmethod
    def normalize_items(cls, v: object) -> object:
        if not isinstance(v, list):
            return v
        cleaned: list[str] = []
        for item in v:
            s = str(item).strip()
            s = re.sub(r"^\d+[.)]\s*", "", s, count=1)
            s = re.sub(r"^[\s\u2022*\-]+", "", s).strip()
            if s:
                cleaned.append(s)
        return cleaned

    @field_validator("questions")
    @classmethod
    def each_non_empty(cls, v: list[str]) -> list[str]:
        for q in v:
            if not q.strip():
                raise ValueError("Each question must be non-empty.")
        return v


class ConsensusOut(BaseModel):
    """Structured consensus output validated *inside* the consensus output guardrail.

    Deliberately not set as ``consensus_agent.output_type`` — keeping the agent's
    return type as a raw string preserves tolerance for fenced / prose JSON from
    OpenRouter providers (same reason ``intake_agent`` does not set ``output_type``).
    The guardrail parses the raw string, then validates with this model.
    """

    primaryDiagnosis: str
    icdCode: str  # may be empty string when the model is uncertain — handled in the guardrail
    confidence: int = Field(ge=0, le=100)
    differentials: list[str]
    prognosis: str
    keyFindings: str
    urgency: Literal["routine", "urgent", "emergent"]

    model_config = {"extra": "allow"}  # ride out future field additions without tripping


def _strip_json_fences(raw: str) -> str:
    return re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()


def _try_parse_questions_json_dict(text: str) -> dict | None:
    """Extract a JSON object that may contain `questions`, tolerating fences and surrounding prose."""
    clean = _strip_json_fences(text)
    try:
        data = json.loads(clean)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", clean)
    if match:
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass
    return None


def _questions_from_prose(text: str) -> list[str]:
    """Split model prose (paragraphs or lines) into up to four question strings."""
    t = text.strip()
    blocks = [b.strip() for b in re.split(r"\n\s*\n+", t) if b.strip()]
    if len(blocks) < 4:
        blocks = [ln.strip() for ln in t.splitlines() if ln.strip()]
    prefix_re = re.compile(
        r"^\s*(?:[\u2022*\-]\s*)?(?:\d+[.)]\s*|(?:question|q)\s*\d+[:.)]?\s*)",
        re.IGNORECASE,
    )
    cleaned: list[str] = []
    for b in blocks:
        s = prefix_re.sub("", b).strip()
        s = re.sub(r"^[\s\u2022*\-]+", "", s).strip()
        if s:
            cleaned.append(s)
        if len(cleaned) >= 4:
            break
    return cleaned[:4]


def parse_intake_followup_text(text: str) -> IntakeFollowupOut:
    """
    Build IntakeFollowupOut from model output: strict JSON with `questions`, or plain prose
    (OpenRouter often returns the latter; SDK output_type would raise ModelBehaviorError).
    """
    if not (text or "").strip():
        raise ValueError("Empty intake model response")

    blob = _try_parse_questions_json_dict(text)
    if blob is not None and "questions" in blob:
        try:
            return IntakeFollowupOut.model_validate(blob)
        except ValidationError:
            pass

    prose_qs = _questions_from_prose(text)
    if len(prose_qs) == 4:
        return IntakeFollowupOut(questions=prose_qs)

    raise ValueError(
        "Could not parse four follow-up questions from model output "
        f"(JSON with 'questions' or four lines/paragraphs). Got {len(prose_qs)} segments."
    )


_PUBMED_PMID_RE = re.compile(
    r"(?:pubmed\.ncbi\.nlm\.nih\.gov/|ncbi\.nlm\.nih\.gov/pubmed/)(\d+)",
    re.IGNORECASE,
)
_INLINE_PMID_RE = re.compile(r"\bPMID\s*[:#]?\s*(\d+)\b", re.IGNORECASE)


def _extract_json_robust(raw: str) -> dict | list | None:
    """
    Same extraction strategy as `main.parse_json` (fences → full string → first {...} or [...]),
    but returns None instead of raising so callers can fall back.
    """
    clean = re.sub(r"```(?:json)?\s*", "", (raw or "").strip()).replace("```", "").strip()
    if not clean:
        return None
    try:
        v = json.loads(clean)
        return v if isinstance(v, (dict, list)) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", clean)
    if match:
        try:
            v = json.loads(match.group(1))
            return v if isinstance(v, (dict, list)) else None
        except json.JSONDecodeError:
            pass
    return None


def _try_parse_json_blob(text: str) -> dict | list | None:
    """Second-pass JSON slice (object-only, then array-only) if `_extract_json_robust` missed."""
    clean = _strip_json_fences((text or "").strip())
    if not clean:
        return None
    for pattern in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
        match = re.search(pattern, clean)
        if match:
            try:
                v = json.loads(match.group(0))
                return v if isinstance(v, (dict, list)) else None
            except json.JSONDecodeError:
                continue
    return None


def _extract_pmids_from_text(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for rx in (_PUBMED_PMID_RE, _INLINE_PMID_RE):
        for m in rx.finditer(text):
            pid = m.group(1)
            if pid not in seen:
                seen.add(pid)
                out.append(pid)
    return out


def _coerce_year(y: object) -> int | str:
    if isinstance(y, int):
        return y
    if isinstance(y, float) and y.is_integer():
        return int(y)
    if isinstance(y, str) and y.strip().isdigit():
        return int(y.strip())
    return str(y).strip() if y is not None else "—"


def _first_str(d: dict, *keys: str) -> str:
    for k in keys:
        if k not in d or d[k] is None:
            continue
        s = str(d[k]).strip()
        if s:
            return s
    return ""


def _normalize_paper_dict(p: object) -> dict | None:
    """Map varied model keys to the API shape; keep entries that can yield a useful link or title."""
    if not isinstance(p, dict):
        return None

    title = _first_str(p, "title", "Title", "paper_title", "name", "article_title")
    pmid = _first_str(p, "pmid", "PMID", "pubmed_id", "PubMedID", "pubmedId")
    if pmid and not pmid.isdigit():
        pmid = ""

    url = _first_str(p, "url", "URL", "link", "href", "uri")
    doi = _first_str(p, "doi", "DOI")

    if url:
        m = _PUBMED_PMID_RE.search(url)
        if m and not pmid:
            pmid = m.group(1)
        if pmid.isdigit():
            url = re.sub(r"\{pmid\}", pmid, url, flags=re.IGNORECASE)
            if re.search(r"\{pmid\}", url, re.IGNORECASE):
                url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    if doi and not url:
        url = doi if doi.lower().startswith("http") else f"https://doi.org/{doi}"

    summary = _first_str(p, "summary", "abstract", "Abstract", "snippet", "description")
    authors = _first_str(p, "authors", "Authors", "author", "first_author") or "—"
    journal = _first_str(p, "journal", "Journal", "source", "venue") or "—"
    year = _coerce_year(p.get("year", p.get("Year")))
    relevance = _first_str(p, "relevance", "why", "rationale") or "See summary."

    if not title and not pmid and not url:
        if len(summary) >= 24:
            title = (summary[:77] + "…") if len(summary) > 80 else summary
        else:
            return None

    if not title:
        title = f"Reference (PMID {pmid})" if pmid.isdigit() else "Reference"

    if pmid.isdigit() and not url:
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    if pmid.isdigit() and url and "pubmed" in url.lower() and not _PUBMED_PMID_RE.search(url):
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

    if not summary:
        summary = "—"

    return {
        "title": title,
        "authors": authors,
        "journal": journal,
        "year": year,
        "relevance": relevance,
        "summary": summary,
        "pmid": pmid if pmid.isdigit() else "",
        "url": url or (f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid.isdigit() else ""),
    }


def parse_research_papers(raw: str) -> tuple[list[dict], str | None]:
    """
    Parse research agent output into paper dicts for the API. Never raises — returns
    fallbacks so the pipeline can continue when the model returns prose or broken JSON.
    """
    text = (raw or "").strip()
    if not text:
        return [], "Research model returned an empty response."

    blob = _extract_json_robust(text) or _try_parse_json_blob(text)
    papers_raw: list | None = None
    if isinstance(blob, dict):
        for key in ("papers", "references", "research_papers", "articles", "results"):
            v = blob.get(key)
            if isinstance(v, list) and v:
                papers_raw = v
                break
    elif isinstance(blob, list):
        papers_raw = blob

    papers: list[dict] = []
    if isinstance(papers_raw, list):
        for item in papers_raw[:12]:
            norm = _normalize_paper_dict(item)
            if norm:
                papers.append(norm)

    if papers:
        return papers[:8], None

    pmids = _extract_pmids_from_text(text)
    if pmids:
        stub = [
            {
                "title": f"PubMed citation (PMID {pid})",
                "authors": "Open on PubMed to verify authors and journal.",
                "journal": "—",
                "year": "—",
                "relevance": "PMID extracted from the model response; confirm relevance to this case on PubMed.",
                "summary": "Structured JSON was not returned; only the identifier was recovered from the text.",
                "pmid": pid,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
            }
            for pid in pmids[:8]
        ]
        return stub, "Model did not return valid JSON; PubMed links were recovered from the text."

    papers = [
        {
            "title": "Literature review (unstructured response)",
            "authors": "—",
            "journal": "—",
            "year": "—",
            "relevance": "The research specialist did not return parseable JSON; this is the raw narrative.",
            "summary": text[:4000] + ("…" if len(text) > 4000 else ""),
            "pmid": "",
            "url": "",
        }
    ]
    return papers, "Model did not return valid JSON with a papers array; showing narrative fallback (no PubMed links)."
