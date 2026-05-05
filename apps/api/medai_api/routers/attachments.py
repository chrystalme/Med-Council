"""Attachment routes — multipart upload + listing + delete.

Three routes scoped to a case:

  POST   /api/cases/{case_id}/attachments               upload (multipart) or paste text
  GET    /api/cases/{case_id}/attachments               list metadata
  DELETE /api/cases/{case_id}/attachments/{id}          delete

Ownership is enforced at every entry — `cases.user_id` must match the
caller before any read/write touches `case_attachments`.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from .. import db as _db
from ..auth import AuthUser, current_user_maybe_required
from ..helpers import cases_user_id

router = APIRouter()


@router.post("/api/cases/{case_id}/attachments")
async def create_attachment(
    case_id: str,
    kind: str = Form("file"),
    question_index: int | None = Form(default=None),
    text: str = Form(""),
    file: UploadFile | None = File(default=None),
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    """Attach a test-result file or pasted text to a follow-up question on a case.

    Body is multipart/form-data. Either `file` OR `text` must be non-empty.
    """
    from attachments import (
        AttachmentStoreError,
        extract_text,
        get_attachment_store,
        is_mime_supported,
    )
    from auth import effective_plan

    if kind not in ("file", "pasted"):
        raise HTTPException(status_code=400, detail="kind must be 'file' or 'pasted'.")

    user_id = cases_user_id(user)
    plan = effective_plan(user)

    # Verify the case belongs to this user.
    con = _db.connect()
    try:
        row = con.execute(
            "SELECT user_id FROM cases WHERE id = %s", (case_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Case not found.")
        if (row["user_id"] or "") != user_id:
            raise HTTPException(status_code=403, detail="This case does not belong to you.")

        filename: str | None = None
        mime: str | None = None
        blob: bytes | None = None
        payload_text = text or ""

        if kind == "file":
            if file is None:
                raise HTTPException(status_code=400, detail="kind='file' requires a `file` field.")
            blob = await file.read()
            filename = file.filename or "attachment"
            mime = file.content_type or "application/octet-stream"
            if not is_mime_supported(mime):
                raise HTTPException(
                    status_code=415,
                    detail={
                        "code": "attachment_type",
                        "message": f"Unsupported file type: {mime}",
                    },
                )
            payload_text = extract_text(blob, mime, filename)
        else:
            payload_text = payload_text.strip()
            if not payload_text:
                raise HTTPException(status_code=400, detail="kind='pasted' requires non-empty `text`.")

        try:
            store = get_attachment_store()
            row_out = store.save(
                con,
                case_id=case_id,
                user_id=user_id,
                user_plan=plan,
                kind=kind,  # type: ignore[arg-type]
                filename=filename,
                mime_type=mime,
                blob=blob,
                text=payload_text,
                question_index=question_index,
            )
        except AttachmentStoreError as exc:
            raise HTTPException(
                status_code=402,
                detail={"code": exc.code, "message": exc.message, **exc.ctx},
            ) from exc

        return {
            "id": row_out.id,
            "filename": row_out.filename,
            "mime_type": row_out.mime_type,
            "kind": row_out.kind,
            "size_bytes": row_out.size_bytes,
            "text_preview": (row_out.text or "")[:400],
            "question_index": row_out.question_index,
            "created_at": row_out.created_at,
        }
    finally:
        con.close()


@router.get("/api/cases/{case_id}/attachments")
async def list_attachments(
    case_id: str,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    from attachments import get_attachment_store

    user_id = cases_user_id(user)
    con = _db.connect()
    try:
        row = con.execute("SELECT user_id FROM cases WHERE id = %s", (case_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Case not found.")
        if (row["user_id"] or "") != user_id:
            raise HTTPException(status_code=403, detail="This case does not belong to you.")

        rows = get_attachment_store().list_for_case(con, case_id)
        return {
            "attachments": [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "filename": r.filename,
                    "mime_type": r.mime_type,
                    "size_bytes": r.size_bytes,
                    "text_preview": (r.text or "")[:400],
                    "question_index": r.question_index,
                    "created_at": r.created_at,
                }
                for r in rows
            ]
        }
    finally:
        con.close()


@router.delete("/api/cases/{case_id}/attachments/{attachment_id}")
async def delete_attachment(
    case_id: str,
    attachment_id: str,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    from attachments import get_attachment_store

    user_id = cases_user_id(user)
    con = _db.connect()
    try:
        ok = get_attachment_store().delete(con, attachment_id, user_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Attachment not found.")
        return {"ok": True}
    finally:
        con.close()
