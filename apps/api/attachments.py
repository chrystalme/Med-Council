"""
Case attachment storage + text extraction.

Default: **Postgres** — blobs live in a `bytea` column of `case_attachments`,
with extracted text and metadata alongside. Fine at this stage; for production
we'll move blobs to GCS via `storage.get_storage()` and keep only the metadata
row in Postgres (see `GcsAttachmentStore` stub).

Switch via `ATTACHMENT_STORE=postgres|gcs`. Default: postgres.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

log = logging.getLogger("medai.attachments")

# MIME types we accept. Anything else → 415 upstream.
TEXT_MIMES = {"text/plain", "text/markdown", "text/csv", "application/json"}
PDF_MIME = "application/pdf"
IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


@dataclass
class AttachmentRow:
    id: str
    case_id: str
    user_id: str
    kind: Literal["file", "pasted"]
    filename: str | None
    mime_type: str | None
    text: str
    size_bytes: int
    question_index: int | None
    created_at: str


class AttachmentStoreError(Exception):
    """Raised for tier cap or validation failures. Carries a structured code."""

    def __init__(self, code: str, message: str, **ctx: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.ctx = ctx


FREE_PER_CASE_LIMIT = 5
PRO_PER_CASE_LIMIT = 20
FREE_SIZE_LIMIT = 1 * 1024 * 1024  # 1 MB
PRO_SIZE_LIMIT = 10 * 1024 * 1024  # 10 MB


def extract_text(blob: bytes, mime_type: str, filename: str) -> str:
    """Best-effort text extraction. Returns a placeholder note for unsupported cases."""
    if not blob:
        return ""

    if mime_type in TEXT_MIMES or mime_type.startswith("text/"):
        return blob.decode("utf-8", errors="replace").strip()

    if mime_type == PDF_MIME:
        try:
            from pypdf import PdfReader
            from io import BytesIO

            reader = PdfReader(BytesIO(blob))
            pages = []
            for page in reader.pages:
                try:
                    pages.append(page.extract_text() or "")
                except Exception as exc:
                    log.warning("pdf page extraction failed: %s", exc)
            return "\n\n".join(p.strip() for p in pages if p and p.strip())
        except Exception as exc:
            log.warning("pdf extraction failed for %s: %s", filename, exc)
            return f"[PDF attached: {filename} — text extraction failed]"

    if mime_type in IMAGE_MIMES or mime_type.startswith("image/"):
        return f"[Image attached: {filename} — OCR not yet available; patient should describe if relevant]"

    return f"[File attached: {filename} (type {mime_type})]"


def is_mime_supported(mime_type: str) -> bool:
    if mime_type in TEXT_MIMES or mime_type.startswith("text/"):
        return True
    if mime_type == PDF_MIME:
        return True
    if mime_type in IMAGE_MIMES or mime_type.startswith("image/"):
        return True
    return False


class AttachmentStore(Protocol):
    def ensure_schema(self, con) -> None: ...
    def save(
        self,
        con,
        *,
        case_id: str,
        user_id: str,
        user_plan: Literal["free", "pro"],
        kind: Literal["file", "pasted"],
        filename: str | None,
        mime_type: str | None,
        blob: bytes | None,
        text: str,
        question_index: int | None,
    ) -> AttachmentRow: ...
    def list_for_case(self, con, case_id: str) -> list[AttachmentRow]: ...
    def get_texts_for_case(self, con, case_id: str) -> list[AttachmentRow]: ...
    def delete(self, con, attachment_id: str, user_id: str) -> bool: ...


class PostgresAttachmentStore:
    def ensure_schema(self, con) -> None:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS case_attachments (
                id             TEXT PRIMARY KEY,
                case_id        TEXT NOT NULL,
                user_id        TEXT NOT NULL,
                kind           TEXT NOT NULL,
                filename       TEXT,
                mime_type      TEXT,
                blob           BYTEA,
                text           TEXT NOT NULL DEFAULT '',
                size_bytes     INTEGER NOT NULL DEFAULT 0,
                question_index INTEGER,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_attachments_case ON case_attachments(case_id)"
        )
        con.commit()

    def _count_for_case(self, con, case_id: str) -> int:
        row = con.execute(
            "SELECT COUNT(*) AS n FROM case_attachments WHERE case_id = %s",
            (case_id,),
        ).fetchone()
        return int(row["n"]) if row else 0

    def save(
        self,
        con,
        *,
        case_id: str,
        user_id: str,
        user_plan: Literal["free", "pro"],
        kind: Literal["file", "pasted"],
        filename: str | None,
        mime_type: str | None,
        blob: bytes | None,
        text: str,
        question_index: int | None,
    ) -> AttachmentRow:
        count_cap = PRO_PER_CASE_LIMIT if user_plan == "pro" else FREE_PER_CASE_LIMIT
        size_cap = PRO_SIZE_LIMIT if user_plan == "pro" else FREE_SIZE_LIMIT

        size_bytes = len(blob) if blob else len((text or "").encode("utf-8"))

        if size_bytes > size_cap:
            raise AttachmentStoreError(
                "attachment_size",
                f"Attachment is {size_bytes} bytes; the {user_plan} tier limit is {size_cap}.",
                size_bytes=size_bytes,
                cap=size_cap,
                tier=user_plan,
            )

        existing = self._count_for_case(con, case_id)
        if existing >= count_cap:
            raise AttachmentStoreError(
                "attachment_cap",
                f"This case already has {existing} attachments; the {user_plan} tier limit is {count_cap}.",
                current=existing,
                cap=count_cap,
                tier=user_plan,
            )

        attachment_id = f"att_{uuid.uuid4().hex[:24]}"
        now = datetime.now(timezone.utc).isoformat()

        con.execute(
            """
            INSERT INTO case_attachments
              (id, case_id, user_id, kind, filename, mime_type, blob, text, size_bytes, question_index, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                attachment_id,
                case_id,
                user_id,
                kind,
                filename,
                mime_type,
                blob,
                text or "",
                size_bytes,
                question_index,
                now,
            ),
        )
        con.commit()

        return AttachmentRow(
            id=attachment_id,
            case_id=case_id,
            user_id=user_id,
            kind=kind,
            filename=filename,
            mime_type=mime_type,
            text=text or "",
            size_bytes=size_bytes,
            question_index=question_index,
            created_at=now,
        )

    def list_for_case(self, con, case_id: str) -> list[AttachmentRow]:
        rows = con.execute(
            """
            SELECT id, case_id, user_id, kind, filename, mime_type, text, size_bytes, question_index, created_at
            FROM case_attachments
            WHERE case_id = %s
            ORDER BY created_at ASC
            """,
            (case_id,),
        ).fetchall()
        return [
            AttachmentRow(
                id=r["id"],
                case_id=r["case_id"],
                user_id=r["user_id"],
                kind=r["kind"],
                filename=r["filename"],
                mime_type=r["mime_type"],
                text=r["text"] or "",
                size_bytes=int(r["size_bytes"] or 0),
                question_index=r["question_index"],
                created_at=(
                    r["created_at"].isoformat()
                    if hasattr(r["created_at"], "isoformat")
                    else str(r["created_at"] or "")
                ),
            )
            for r in rows
        ]

    def get_texts_for_case(self, con, case_id: str) -> list[AttachmentRow]:
        """Alias of list_for_case for semantic clarity at call sites that want the text payload."""
        return self.list_for_case(con, case_id)

    def delete(self, con, attachment_id: str, user_id: str) -> bool:
        cur = con.execute(
            "DELETE FROM case_attachments WHERE id = %s AND user_id = %s",
            (attachment_id, user_id),
        )
        con.commit()
        return (cur.rowcount or 0) > 0


class GcsAttachmentStore:
    """Stub — implement on GCP migration.

    Plan: store blob bytes via `storage.get_storage()` keyed by
    `attachments/{user_id}/{id}`; persist metadata + extracted text in Postgres
    (`case_attachments` without the `blob` column, or with `blob` NULL).
    """

    def ensure_schema(self, *args, **kwargs) -> None:
        raise NotImplementedError("GcsAttachmentStore not yet implemented.")

    def save(self, *args, **kwargs) -> AttachmentRow:
        raise NotImplementedError("GcsAttachmentStore not yet implemented.")

    def list_for_case(self, *args, **kwargs) -> list[AttachmentRow]:
        raise NotImplementedError("GcsAttachmentStore not yet implemented.")

    def get_texts_for_case(self, *args, **kwargs) -> list[AttachmentRow]:
        raise NotImplementedError("GcsAttachmentStore not yet implemented.")

    def delete(self, *args, **kwargs) -> bool:
        raise NotImplementedError("GcsAttachmentStore not yet implemented.")


_store: AttachmentStore | None = None


def get_attachment_store() -> AttachmentStore:
    global _store
    if _store is not None:
        return _store
    backend = (os.environ.get("ATTACHMENT_STORE") or "postgres").lower()
    if backend == "gcs":
        _store = GcsAttachmentStore()
    else:
        _store = PostgresAttachmentStore()
    return _store


def format_attachment_block(rows: list[AttachmentRow], question_texts: list[str] | None = None) -> str:
    """Render attachments as a plain-text block suitable for inclusion in agent prompts.

    When `question_texts` is provided, each attachment's `question_index` is
    surfaced as "related to Q{i+1}: {question}" so the agent can correlate
    test results with specific follow-up questions.
    """
    if not rows:
        return ""
    lines = ["--- Test results provided by patient ---"]
    for r in rows:
        header_parts = []
        if r.kind == "file" and r.filename:
            header_parts.append(f"file: {r.filename}")
        else:
            header_parts.append("pasted text")
        if r.question_index is not None and question_texts and 0 <= r.question_index < len(question_texts):
            q = question_texts[r.question_index][:80]
            header_parts.append(f"related to Q{r.question_index + 1}: {q}")
        text = (r.text or "").strip()
        if not text:
            text = "(no extractable text)"
        lines.append(f"[{' · '.join(header_parts)}]\n{text}")
    lines.append("---")
    return "\n\n".join(lines)
