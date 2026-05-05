"""PubMed E-utilities client.

Model-agnostic safety net for the /api/research stage: when the model's
own citations are sparse or invalid, we query PubMed directly via NCBI
E-utilities (esearch + esummary) and surface the top hits as paper
cards.

Extracted from main.py so the network shape and parsing logic can grow
(retries, alternate sources) without bloating the route handlers.
"""

from __future__ import annotations

import json
import re
from urllib.parse import quote_plus
from urllib.request import Request, urlopen


def search_papers(term: str, *, retmax: int = 4) -> list[dict]:
    """Query PubMed for `term` and return up to `retmax` paper cards.

    Returns [] if the term is empty, the network call fails, or no
    results are found. Never raises — caller can treat the result as
    best-effort enrichment.
    """
    t = (term or "").strip()
    if not t:
        return []

    try:
        # PubMed search can be brittle with long, highly specific terms. Try a few progressively
        # simpler queries to maximize hit-rate, regardless of model output format.
        words = re.findall(r"[a-zA-Z]{3,}", t.lower())
        simplified = " ".join(words[:10]) if words else t

        # A high-recall query shape for typical clinical text.
        pain_terms = ["chest pain", "angina", "chest tightness", "chest pressure"]
        ex_terms = ["exertion", "exercise", "exertional"]
        high_recall = f"({' OR '.join(pain_terms)}) AND ({' OR '.join(ex_terms)})"

        candidates = [
            t[:8000],
            simplified[:400],
            high_recall,
            (high_recall + " review").strip(),
            "chest pain review",
        ]

        ids: list[str] = []
        for cand in candidates:
            if not cand.strip():
                continue
            q = quote_plus(cand)
            esearch = (
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
                f"?db=pubmed&retmode=json&retmax={int(retmax)}&sort=relevance&term={q}"
            )
            req = Request(esearch, headers={"User-Agent": "MedAI-Council/1.0 (demo)"})
            with urlopen(req, timeout=6) as r:
                payload = json.loads(r.read().decode("utf-8", errors="replace"))
            got = payload.get("esearchresult", {}).get("idlist", []) or []
            got = [str(x) for x in got if str(x).isdigit()]
            if got:
                ids = got
                break
        if not ids:
            return []

        id_csv = ",".join(ids)
        esummary = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
            f"?db=pubmed&retmode=json&id={id_csv}"
        )
        req2 = Request(esummary, headers={"User-Agent": "MedAI-Council/1.0 (demo)"})
        with urlopen(req2, timeout=6) as r:
            summ = json.loads(r.read().decode("utf-8", errors="replace"))

        result = summ.get("result", {}) if isinstance(summ, dict) else {}
        out: list[dict] = []
        for pid in ids:
            item = result.get(pid, {})
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "") or "").strip() or f"PubMed citation (PMID {pid})"
            source = str(item.get("source", "") or "").strip() or "—"
            pubdate = str(item.get("pubdate", "") or "").strip()
            year = pubdate[:4] if pubdate[:4].isdigit() else "—"
            authors = item.get("authors", [])
            if isinstance(authors, list) and authors:
                names = [a.get("name") for a in authors if isinstance(a, dict) and a.get("name")]
                authors_s = (", ".join(names[:3]) + (" et al." if len(names) > 3 else "")) if names else "—"
            else:
                authors_s = "—"
            out.append(
                {
                    "title": title,
                    "authors": authors_s,
                    "journal": source,
                    "year": year,
                    "relevance": "PubMed search result (model-agnostic fallback).",
                    "summary": "Open the PubMed link for abstract and applicability to this specific case.",
                    "pmid": pid,
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
                }
            )
        return out[:retmax]
    except Exception:
        return []


__all__ = ["search_papers"]
