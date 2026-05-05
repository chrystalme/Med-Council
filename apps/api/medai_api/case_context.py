"""Case-context helpers for agent-stage prompts.

Two functions injected into prompts at council / message stages:

* `retrieve_patient_context` — vector-search over prior consultations and
  render the top-k as a plain-text block.
* `attachment_block_for_case` — list current case attachments (text +
  optional question linkage) as a plain-text block.

Both return `""` when there's nothing to inject so callers can unconditionally
concatenate the result.
"""

from __future__ import annotations

import logging

from . import db as _db
log = logging.getLogger("medai.case_context")

MAX_RETRIEVED_CONSULTATION_CHARS = 4000


def retrieve_patient_context(user_id: str, query: str, top_k: int = 3) -> str:
    """Return a plain-text block of similar prior consultations for injection into agent prompts.

    Returns "" when the user has no prior consultations or embedding fails —
    callers can unconditionally concatenate the result.
    """
    if not user_id or not (query or "").strip():
        return ""
    from embeddings import get_embedding_provider
    from vector_store import get_vector_store

    con = _db.connect()
    try:
        try:
            vec = get_embedding_provider().embed(query)
            hits = get_vector_store().query(
                con,
                embedding=vec,
                top_k=top_k,
                where={"user_id": user_id},
            )
        except Exception as exc:
            log.warning("patient context retrieval failed: %s", exc)
            return ""

        if not hits:
            return ""

        lines = ["--- Patient's prior consultations (most relevant first) ---"]
        for h in hits:
            meta = h.metadata or {}
            date = str(meta.get("created_at") or "")[:10]
            dx = meta.get("primary_dx") or "—"
            urgency = meta.get("urgency") or ""
            conf = meta.get("confidence") or 0
            score_pct = int(round(h.score * 100))
            document = (h.document or "").strip()
            if len(document) > MAX_RETRIEVED_CONSULTATION_CHARS:
                document = document[:MAX_RETRIEVED_CONSULTATION_CHARS].rstrip() + "\n[truncated]"
            lines.append(
                f"[{date} · {dx} (confidence {conf}%, {urgency}) · match {score_pct}%]\n{document}"
            )
        lines.append("---")
        return "\n\n".join(lines)
    finally:
        con.close()


def attachment_block_for_case(
    case_id: str,
    user_id: str,
    question_texts: list[str] | None = None,
) -> str:
    """Read attachments for a case and render as a prompt-safe text block.

    Ownership is enforced at write time by `create_attachment` (the case is
    verified to belong to the requesting user before save). The per-row
    user_id filter below is the single read-time check — sufficient because
    a row exists with `user_id != requesting_user` only if either save's
    ownership check was skipped or somebody wrote rows out-of-band, both of
    which fall back to "filter rejects all rows → empty block".

    Previously this function also ran a `SELECT id FROM cases WHERE id=%s
    AND user_id=%s` upfront. Dropped — that query duplicated the per-row
    filter, added a round-trip per agent stage, and was the dominant cost
    on Cloud Run cold starts (~5–10ms × stages).
    """
    from attachments import format_attachment_block, get_attachment_store

    if not user_id:
        return ""

    con = _db.connect()
    try:
        rows = [
            row
            for row in get_attachment_store().list_for_case(con, case_id)
            if row.user_id == user_id
        ]
    except NotImplementedError:
        return ""
    finally:
        con.close()
    return format_attachment_block(rows, question_texts)
