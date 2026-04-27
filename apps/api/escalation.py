"""Step 4 — notify on-call when consensus urgency warrants escalation (Resend).

Also hosts the Pro-tier `send_patient_email` helper used by the workspace's
"Email to patient" action. Both use the same Resend transport.
"""

from __future__ import annotations

import html as _html
import json
import logging
import math
import os
import urllib.error
import urllib.request

log = logging.getLogger("medai.escalation")

# Terms the consensus agent's `urgency` field may carry that should page
# on-call. The canonical outputs from `consensus_agent` in council.py are
# "routine" | "urgent" | "emergent". "routine" is deliberately NOT in this
# set — pages should only fire on elevated urgency. Entries are matched
# after `.strip().lower()`.
URGENT_VALUES = frozenset(
    {
        "emergent",
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


def is_urgent(consensus: dict) -> bool:
    """True when consensus urgency warrants an on-call page."""
    return _urgency_from_consensus(consensus) in URGENT_VALUES


def _safe_subject_part(value: object, max_len: int = 120) -> str:
    """Strip control chars/CRLF and cap length for safe inclusion in email subjects."""
    return "".join(c for c in str(value) if c.isprintable())[:max_len].strip()


def _mask_email(addr: str) -> str:
    """Best-effort masking of an email for INFO logs (e.g. ``ali***@clinic.com``)."""
    addr = (addr or "").strip()
    if "@" not in addr:
        return "***"
    local, _, domain = addr.partition("@")
    return (local[:3] + "***@" + domain) if local else "***@" + domain


def _resolve_recipient(addr: str) -> str:
    """Honour EMAIL_OVERRIDE_TO so dev/sandbox envs can route every send to one
    inbox without verifying a domain in Resend. Logs the redirect so the
    intended recipient is still visible.
    """
    override = os.environ.get("EMAIL_OVERRIDE_TO", "").strip()
    if override and override != (addr or "").strip():
        log.info(
            "EMAIL_OVERRIDE_TO active — redirecting %s -> %s",
            _mask_email(addr), _mask_email(override),
        )
        return override
    return addr


def maybe_escalate_oncall(*, consensus: dict, symptoms: str) -> None:
    """
    Fire-and-forget email via Resend when RESEND_API_KEY and ONCALL_DOCTOR_EMAIL are set
    and consensus urgency looks high.
    """
    key = os.environ.get("RESEND_API_KEY", "").strip()
    to = os.environ.get("ONCALL_DOCTOR_EMAIL", "").strip()
    from_addr = os.environ.get("RESEND_FROM_EMAIL", "").strip()
    if not key or not to or not from_addr:
        log.info(
            "escalation skipped — Resend not configured (key=%s to=%s from=%s)",
            "set" if key else "missing",
            "set" if to else "missing",
            "set" if from_addr else "missing",
        )
        return

    urg = _urgency_from_consensus(consensus)
    if urg not in URGENT_VALUES:
        # Silent skips bit us once (the consensus agent emits "emergent",
        # which wasn't in the allowlist). Log every non-match so the next
        # vocabulary drift is visible in Cloud Logging rather than silent.
        log.info(
            "escalation skipped — urgency=%r not in URGENT_VALUES=%s",
            urg, sorted(URGENT_VALUES),
        )
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
            "to": [_resolve_recipient(to)],
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


# ─────────────────────────────────────────────────────────────────────────────
#  Patient-facing email (Pro only)
# ─────────────────────────────────────────────────────────────────────────────


class ResendNotConfiguredError(RuntimeError):
    """Raised when RESEND_API_KEY or RESEND_FROM_EMAIL are missing."""


def _md_to_html(md: str) -> str:
    """Tiny, dependency-free markdown→HTML for the subset our agents emit.

    Handles: blank-line paragraphs, **bold**, *italics*, lines starting with
    `- ` as bulleted lists, `# ... ###` as headings. Everything else is
    escaped so the email renders safely.
    """
    if not md:
        return ""
    lines = md.splitlines()
    out: list[str] = []
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    def inline(s: str) -> str:
        s = _html.escape(s)
        # Bold **x**
        import re as _re

        s = _re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        # Italics *x* (non-greedy, not touching bold we just wrapped)
        s = _re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)
        return s

    para: list[str] = []

    def flush_para() -> None:
        if para:
            out.append(
                "<p style=\"margin:0 0 12px 0;line-height:1.55;color:#1a2348\">"
                + " ".join(inline(x) for x in para)
                + "</p>"
            )
            para.clear()

    for raw in lines:
        line = raw.rstrip()
        stripped = line.lstrip()

        if not stripped:
            flush_para()
            close_list()
            continue

        if stripped.startswith("### "):
            flush_para()
            close_list()
            out.append(
                "<h3 style=\"font-family:Georgia,serif;color:#1a2348;margin:18px 0 8px 0;font-size:17px\">"
                + inline(stripped[4:])
                + "</h3>"
            )
            continue
        if stripped.startswith("## "):
            flush_para()
            close_list()
            out.append(
                "<h2 style=\"font-family:Georgia,serif;color:#1a2348;margin:22px 0 10px 0;font-size:20px\">"
                + inline(stripped[3:])
                + "</h2>"
            )
            continue
        if stripped.startswith("# "):
            flush_para()
            close_list()
            out.append(
                "<h1 style=\"font-family:Georgia,serif;color:#3d52a0;margin:24px 0 12px 0;font-size:24px\">"
                + inline(stripped[2:])
                + "</h1>"
            )
            continue

        if stripped.startswith("- ") or stripped.startswith("* "):
            flush_para()
            if not in_list:
                out.append(
                    "<ul style=\"padding-left:20px;margin:0 0 12px 0;color:#1a2348\">"
                )
                in_list = True
            out.append(
                "<li style=\"margin-bottom:6px;line-height:1.55\">"
                + inline(stripped[2:])
                + "</li>"
            )
            continue

        para.append(stripped)

    flush_para()
    close_list()
    return "\n".join(out)


def _render_patient_html(
    *,
    patient_name: str | None,
    primary_dx: str | None,
    urgency: str | None,
    confidence: int | float | None,
    plan_md: str,
    message_md: str,
    disclaimer: str,
) -> str:
    dx_badge = ""
    if primary_dx:
        dx_badge = (
            f"<p style=\"margin:4px 0 0 0;font-size:13px;color:#5a6690\">"
            f"<strong>Primary assessment:</strong> {_html.escape(primary_dx)}"
        )
        if confidence is not None:
            dx_badge += f" &middot; confidence {int(confidence)}%"
        if urgency:
            dx_badge += f" &middot; {_html.escape(str(urgency))}"
        dx_badge += "</p>"

    greeting = (
        f"Hello{_html.escape(' ' + patient_name) if patient_name else ''},"
    )

    return f"""<!doctype html>
<html>
<head><meta charset="utf-8"><title>Your MedAI Council consultation</title></head>
<body style="margin:0;padding:0;background:#ede8f5;font-family:-apple-system,'Segoe UI',sans-serif;color:#1a2348">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#ede8f5;padding:32px 12px">
    <tr><td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border:1px solid #cfd3e4;border-radius:16px;overflow:hidden;max-width:600px;width:100%">
        <tr><td style="padding:28px 32px 16px 32px;border-bottom:1px solid #e1dcef">
          <p style="margin:0;font-size:12px;letter-spacing:0.18em;text-transform:uppercase;color:#3d52a0">MedAI Council &middot; Consultation Summary</p>
          <h1 style="margin:6px 0 0 0;font-family:Georgia,serif;font-size:26px;color:#1a2348">{greeting}</h1>
          {dx_badge}
        </td></tr>
        <tr><td style="padding:24px 32px">
          <h2 style="font-family:Georgia,serif;color:#3d52a0;margin:0 0 12px 0;font-size:20px">Your summary</h2>
          {_md_to_html(message_md)}
        </td></tr>
        <tr><td style="padding:4px 32px 24px 32px;border-top:1px solid #e1dcef">
          <h2 style="font-family:Georgia,serif;color:#3d52a0;margin:18px 0 12px 0;font-size:20px">Coordinated plan</h2>
          {_md_to_html(plan_md)}
        </td></tr>
        <tr><td style="padding:18px 32px 24px 32px;background:#f6f3fa;border-top:1px solid #e1dcef">
          <p style="margin:0;font-size:12px;color:#5a6690;line-height:1.5">
            <strong>Important:</strong> {_html.escape(disclaimer)}
          </p>
        </td></tr>
      </table>
      <p style="margin:16px 0 0 0;font-size:11px;color:#8697c4;letter-spacing:0.1em;text-transform:uppercase">
        MedAI Council &middot; a research artefact
      </p>
    </td></tr>
  </table>
</body>
</html>"""


def notify_doctor_with_message(
    *,
    doctor_email: str,
    consensus: dict,
    plan_md: str,
    patient_message: str,
    symptoms: str,
) -> str:
    """Email the patient-facing message + plan to a clinician for immediate follow-up.

    Returns one of: ``"sent"`` (dispatched), ``"skipped"`` (preconditions not
    met — Resend not configured, recipient missing, or urgency routine) or
    ``"failed"`` (Resend rejected or transport error). Callers can use this
    to drive distinct UI states — a clinician needs to know when an *attempted*
    page failed vs. when none was warranted.
    """
    key = os.environ.get("RESEND_API_KEY", "").strip()
    from_addr = os.environ.get("RESEND_FROM_EMAIL", "").strip()
    to = (doctor_email or "").strip()
    if not key or not from_addr or not to:
        log.info(
            "doctor notify skipped — Resend or address missing (key=%s from=%s to=%s)",
            "set" if key else "missing",
            "set" if from_addr else "missing",
            "set" if to else "missing",
        )
        return "skipped"

    if not is_urgent(consensus):
        log.info("doctor notify skipped — urgency not in URGENT_VALUES")
        return "skipped"

    urg = _urgency_from_consensus(consensus)
    dx = consensus.get("primaryDiagnosis") or consensus.get("primary_diagnosis") or "—"
    icd = consensus.get("icdCode") or consensus.get("icd_code") or ""
    confidence_raw = consensus.get("confidence")
    confidence: int | None = None
    if isinstance(confidence_raw, (int, float)):
        try:
            if math.isfinite(float(confidence_raw)):
                confidence = int(confidence_raw)
        except (TypeError, ValueError):
            confidence = None

    safe_urg = _safe_subject_part(urg, max_len=24)
    safe_dx = _safe_subject_part(dx, max_len=120)
    subject = f"[MedAI Council] {safe_urg.upper()} — {safe_dx} — patient follow-up needed"
    header_meta = (
        f"<p style=\"margin:0 0 6px 0;font-size:13px;color:#5a6690\">"
        f"<strong>Urgency:</strong> {_html.escape(urg)}"
        + (f" &middot; ICD {_html.escape(str(icd))}" if icd else "")
        + (f" &middot; confidence {confidence}%" if confidence is not None else "")
        + "</p>"
    )

    html = f"""<!doctype html>
<html><body style="font-family:-apple-system,'Segoe UI',sans-serif;color:#1a2348;background:#ede8f5;padding:24px">
  <div style="max-width:640px;margin:0 auto;background:#fff;border:1px solid #cfd3e4;border-radius:14px;overflow:hidden">
    <div style="padding:20px 24px;border-bottom:1px solid #e1dcef;background:#f6f3fa">
      <p style="margin:0;font-size:11px;letter-spacing:0.18em;text-transform:uppercase;color:#3d52a0">MedAI Council &middot; Doctor referral</p>
      <h1 style="margin:6px 0 0 0;font-family:Georgia,serif;font-size:20px;color:#1a2348">{_html.escape(str(dx))}</h1>
      {header_meta}
    </div>
    <div style="padding:20px 24px">
      <h2 style="margin:0 0 8px 0;font-size:15px;color:#3d52a0">Patient symptoms</h2>
      <pre style="white-space:pre-wrap;font-size:13px;background:#f6f3fa;padding:10px;border-radius:8px">{_html.escape(symptoms[:4000])}</pre>
      <h2 style="margin:18px 0 8px 0;font-size:15px;color:#3d52a0">Patient-facing message</h2>
      {_md_to_html(patient_message)}
      <h2 style="margin:18px 0 8px 0;font-size:15px;color:#3d52a0">Coordinated plan</h2>
      {_md_to_html(plan_md)}
      <h2 style="margin:18px 0 8px 0;font-size:15px;color:#3d52a0">Consensus JSON</h2>
      <pre style="white-space:pre-wrap;font-size:12px;background:#f6f3fa;padding:10px;border-radius:8px">{_html.escape(json.dumps(consensus, indent=2)[:12000])}</pre>
    </div>
  </div>
</body></html>"""

    payload = json.dumps(
        {"from": from_addr, "to": [_resolve_recipient(to)], "subject": subject, "html": html}
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
        log.info("doctor referral email sent to %s (urgency=%s)", _mask_email(to), urg)
        return "sent"
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        log.warning("Resend HTTP error %s for %s: %s", exc.code, _mask_email(to), body)
        return "failed"
    except Exception as exc:
        log.warning("Doctor referral send failed for %s: %s", _mask_email(to), exc)
        return "failed"


def send_patient_email(
    *,
    to: str,
    patient_name: str | None = None,
    subject: str | None = None,
    primary_dx: str | None = None,
    urgency: str | None = None,
    confidence: int | float | None = None,
    plan_md: str = "",
    message_md: str = "",
    reply_to: str | None = None,
    disclaimer: str = (
        "This email is a summary generated by a clinical AI system and is not a "
        "substitute for licensed medical advice. Discuss any changes with a clinician."
    ),
) -> dict:
    """Send the plan + patient message as a formatted HTML email via Resend.

    Raises:
        ResendNotConfiguredError when RESEND_API_KEY / RESEND_FROM_EMAIL are missing.
        RuntimeError on send failure.
    """
    key = os.environ.get("RESEND_API_KEY", "").strip()
    from_addr = os.environ.get("RESEND_FROM_EMAIL", "").strip()
    if not key or not from_addr:
        raise ResendNotConfiguredError(
            "RESEND_API_KEY and RESEND_FROM_EMAIL must be set to send patient emails."
        )

    if not subject:
        subject = "Your MedAI Council consultation summary"
        if primary_dx:
            subject = f"Your consultation summary — {primary_dx}"

    html_body = _render_patient_html(
        patient_name=patient_name,
        primary_dx=primary_dx,
        urgency=urgency,
        confidence=confidence,
        plan_md=plan_md,
        message_md=message_md,
        disclaimer=disclaimer,
    )

    payload: dict = {
        "from": from_addr,
        "to": [_resolve_recipient(to)],
        "subject": subject,
        "html": html_body,
    }
    if reply_to:
        payload["reply_to"] = [reply_to]

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        log.info("patient email sent via Resend to %s", to)
        try:
            return json.loads(body)
        except Exception:
            return {"ok": True}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        log.warning("Resend HTTP error %s: %s", exc.code, body)
        raise RuntimeError(f"Resend rejected the email: {body}") from exc
    except Exception as exc:
        log.warning("Resend send failed: %s", exc)
        raise RuntimeError(f"Email send failed: {exc}") from exc
