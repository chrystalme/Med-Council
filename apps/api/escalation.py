"""Step 4 — notify on-call when consensus urgency warrants escalation (Resend)."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger("medai.escalation")

URGENT_VALUES = frozenset(
    {
        "emergency",
        "stat",
        "immediate",
        "critical",
        "urgent",
    }
)


def _urgency_from_consensus(consensus: dict) -> str:
    u = consensus.get("urgency") or consensus.get("urgencyLevel") or ""
    return str(u).strip().lower()


def maybe_escalate_oncall(*, consensus: dict, symptoms: str) -> None:
    """
    Fire-and-forget email via Resend when RESEND_API_KEY and ONCALL_DOCTOR_EMAIL are set
    and consensus urgency looks high.
    """
    key = os.environ.get("RESEND_API_KEY", "").strip()
    to = os.environ.get("ONCALL_DOCTOR_EMAIL", "").strip()
    from_addr = os.environ.get("RESEND_FROM_EMAIL", "").strip()
    if not key or not to or not from_addr:
        return

    urg = _urgency_from_consensus(consensus)
    if urg not in URGENT_VALUES:
        return

    subject = f"[MedAI Council] Escalation — {urg.upper()} urgency"
    dx = consensus.get("primaryDiagnosis") or consensus.get("primary_diagnosis") or "—"
    html = f"""
    <p><strong>Urgency:</strong> {urg}</p>
    <p><strong>Primary diagnosis (draft):</strong> {dx}</p>
    <p><strong>Symptoms excerpt:</strong></p>
    <pre style="white-space:pre-wrap;font-size:13px">{symptoms[:4000]}</pre>
    <p><strong>Full consensus JSON:</strong></p>
    <pre style="white-space:pre-wrap;font-size:12px">{json.dumps(consensus, indent=2)[:12000]}</pre>
    """

    payload = json.dumps(
        {
            "from": from_addr,
            "to": [to],
            "subject": subject,
            "html": html,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        log.info("escalation email sent via Resend (urgency=%s)", urg)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        log.warning("Resend HTTP error %s: %s", e.code, body)
    except Exception as exc:
        log.warning("Resend send failed: %s", exc)
