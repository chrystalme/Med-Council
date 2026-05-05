"""Patient memory (Phase 3) — consultations table + vector retrieval.

Five routes covering the full lifecycle of a saved consultation:

  POST   /api/patient/consultations              save (advisory-locked)
  GET    /api/patient/consultations              list with X-Consultation-Remaining
  GET    /api/patient/consultations/{id}         detail join with case state
  DELETE /api/patient/consultations/{id}         delete + vector unindex
  POST   /api/patient/retrieve                   vector top-k search

Save uses a per-user `pg_advisory_xact_lock` so concurrent Free-tier
saves can't bust `FREE_CONSULTATION_CAP`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

from .. import db as _db
from ..auth import AuthUser, current_user_maybe_required
from ..consultation_memory import build_consultation_memory_text
from ..helpers import cases_user_id, json_object

log = logging.getLogger("medai.consultations")

router = APIRouter()


# ── Module-level constants (kept here to keep main.py free of consultation policy) ──

FREE_CONSULTATION_CAP = 4
MAX_RETRIEVE_DOCUMENT_CHARS = 4000
MAX_CONSULTATION_ATTACHMENT_TEXTS = 20
MAX_CONSULTATION_ATTACHMENT_TEXT_CHARS = 5000
MAX_CONSULTATION_CASE_STATE_CHARS = 80000


def _consultation_count(con, user_id: str) -> int:
    row = con.execute(
        "SELECT COUNT(*) AS n FROM consultations WHERE user_id = %s", (user_id,)
    ).fetchone()
    return int(row["n"]) if row else 0


def _assert_consultation_cap(con, user_id: str, user_plan: str) -> None:
    if user_plan == "pro":
        return
    count = _consultation_count(con, user_id)
    if count >= FREE_CONSULTATION_CAP:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "consultation_cap",
                "cap": FREE_CONSULTATION_CAP,
                "current": count,
                "message": (
                    f"Free tier is limited to {FREE_CONSULTATION_CAP} saved consultations. "
                    "Delete one or upgrade to Pro."
                ),
            },
        )


class ConsultationSaveIn(BaseModel):
    case_id: str
    summary: Annotated[str, Field(min_length=1, max_length=8000)]
    primary_dx: str | None = None
    icd_code: str | None = None
    urgency: str | None = None
    confidence: int | None = None
    attachment_texts: list[str] = Field(default_factory=list)
    case_state: dict[str, Any] | None = None

    @field_validator("attachment_texts")
    @classmethod
    def _bound_attachment_texts(cls, value: list[str]) -> list[str]:
        if len(value) > MAX_CONSULTATION_ATTACHMENT_TEXTS:
            raise ValueError("Too many attachment texts.")
        return [str(text)[:MAX_CONSULTATION_ATTACHMENT_TEXT_CHARS] for text in value]

    @field_validator("case_state")
    @classmethod
    def _bound_case_state(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        encoded = json.dumps(value, ensure_ascii=False)
        if len(encoded) > MAX_CONSULTATION_CASE_STATE_CHARS:
            raise ValueError("Case state is too large.")
        return value


class RetrieveIn(BaseModel):
    query: Annotated[str, Field(min_length=1, max_length=8000)]
    top_k: int = 3


@router.post("/api/patient/consultations")
async def save_consultation(
    req: ConsultationSaveIn,
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    """Persist a finished consultation and index it for vector retrieval.

    Called automatically by the frontend after the consensus completes. Both
    tiers store; Free is capped at FREE_CONSULTATION_CAP.
    """
    from auth import effective_plan
    from embeddings import get_embedding_provider
    from vector_store import get_vector_store

    user_id = cases_user_id(user)
    plan = effective_plan(user)

    con = _db.connect()
    try:
        # psycopg3 with autocommit=False opens an implicit transaction on the
        # first execute; the SELECT, UPDATE, and INSERT below all run inside
        # that single transaction and are committed together. A per-user
        # advisory lock serialises the cap check + INSERT against parallel
        # save_consultation calls from the same user — without it two
        # simultaneous Free-tier saves can both pass the cap check and both
        # INSERT, busting FREE_CONSULTATION_CAP. The lock auto-releases on
        # commit/rollback (xact-scoped).
        lock_key = int.from_bytes(
            hashlib.sha256(user_id.encode("utf-8")).digest()[:8],
            "big",
            signed=True,
        )
        con.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))

        case_row = con.execute(
            "SELECT state FROM cases WHERE id = %s AND user_id = %s",
            (req.case_id, user_id),
        ).fetchone()
        if case_row is None:
            raise HTTPException(status_code=404, detail="Case not found.")

        _assert_consultation_cap(con, user_id, plan)

        consultation_id = f"con_{uuid.uuid4().hex[:24]}"
        now = datetime.now(timezone.utc).isoformat()
        case_state = req.case_state if req.case_state is not None else json_object(case_row["state"])
        case_state_json = json.dumps(case_state, ensure_ascii=False)

        if req.case_state is not None:
            con.execute(
                "UPDATE cases SET state = %s, updated_at = %s WHERE id = %s AND user_id = %s",
                (case_state_json, now, req.case_id, user_id),
            )

        con.execute(
            """
            INSERT INTO consultations
              (id, user_id, case_id, summary, primary_dx, icd_code, urgency, confidence, case_state, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
            """,
            (
                consultation_id,
                user_id,
                req.case_id,
                req.summary,
                req.primary_dx,
                req.icd_code,
                req.urgency,
                req.confidence,
                case_state_json,
                now,
            ),
        )
        con.commit()

        memory_text = build_consultation_memory_text(
            summary=req.summary,
            primary_dx=req.primary_dx,
            icd_code=req.icd_code,
            urgency=req.urgency,
            confidence=req.confidence,
            attachment_texts=req.attachment_texts,
            case_state=case_state,
        )

        try:
            vec = get_embedding_provider().embed(memory_text)
            get_vector_store().upsert(
                con,
                id=consultation_id,
                embedding=vec,
                metadata={
                    "user_id": user_id,
                    "case_id": req.case_id,
                    "created_at": now,
                    "primary_dx": req.primary_dx or "",
                    "urgency": req.urgency or "",
                    "confidence": req.confidence or 0,
                },
                document=memory_text,
            )
        except Exception as exc:
            log.warning("embedding/vector upsert failed; consultation saved without retrieval: %s", exc)

        remaining = None if plan == "pro" else max(0, FREE_CONSULTATION_CAP - _consultation_count(con, user_id))
        if remaining is not None:
            response.headers["X-Consultation-Remaining"] = str(remaining)

        return {
            "id": consultation_id,
            "case_id": req.case_id,
            "created_at": now,
            "remaining": remaining,
        }
    except Exception:
        # Make the rollback explicit so failures surface in psycopg's logs.
        # close() in the finally also rolls back, but explicit is clearer.
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        con.close()


@router.get("/api/patient/consultations")
async def list_consultations(
    response: Response,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    from auth import effective_plan

    user_id = cases_user_id(user)
    plan = effective_plan(user)

    con = _db.connect()
    try:
        rows = con.execute(
            """
            SELECT id, case_id, summary, primary_dx, icd_code, urgency, confidence, created_at
            FROM consultations
            WHERE user_id = %s
            ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()

        if plan != "pro":
            response.headers["X-Consultation-Remaining"] = str(
                max(0, FREE_CONSULTATION_CAP - len(rows))
            )

        return {
            "plan": plan,
            "cap": None if plan == "pro" else FREE_CONSULTATION_CAP,
            "consultations": [
                {
                    "id": r["id"],
                    "case_id": r["case_id"],
                    "summary": r["summary"],
                    "primary_dx": r["primary_dx"],
                    "icd_code": r["icd_code"],
                    "urgency": r["urgency"],
                    "confidence": r["confidence"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
        }
    finally:
        con.close()


@router.post("/api/patient/retrieve")
async def retrieve_consultations(
    req: RetrieveIn,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    from embeddings import get_embedding_provider
    from vector_store import get_vector_store

    user_id = cases_user_id(user)
    if not user_id:
        return {"hits": []}

    con = _db.connect()
    try:
        try:
            vec = get_embedding_provider().embed(req.query)
            hits = get_vector_store().query(
                con,
                embedding=vec,
                top_k=max(1, min(10, int(req.top_k))),
                where={"user_id": user_id},
            )
        except Exception as exc:
            log.warning("retrieval failed: %s", exc)
            return {"hits": []}

        return {
            "hits": [
                {
                    "id": h.id,
                    "score": round(h.score, 4),
                    "metadata": h.metadata,
                    "document": (
                        h.document[:MAX_RETRIEVE_DOCUMENT_CHARS].rstrip() + "\n[truncated]"
                        if len(h.document) > MAX_RETRIEVE_DOCUMENT_CHARS
                        else h.document
                    ),
                }
                for h in hits
            ]
        }
    finally:
        con.close()


@router.get("/api/patient/consultations/{consultation_id}")
async def get_consultation(
    consultation_id: str,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    """Return a single consultation joined with its case state.

    Powers the /patient/consultations/[id] detail view — lets the UI re-render
    the full seven-stage session (intake, follow-up, council, research,
    consensus, plan, message) as tabs without firing a second `/api/cases/…`.
    """
    user_id = cases_user_id(user)
    con = _db.connect()
    try:
        row = con.execute(
            """
            SELECT c.id, c.case_id, c.user_id, c.summary, c.primary_dx, c.icd_code,
                   c.urgency, c.confidence, c.case_state AS consultation_state, c.created_at,
                   cs.state AS case_state, cs.title AS case_title
            FROM consultations c
            LEFT JOIN cases cs ON cs.id = c.case_id AND cs.user_id = c.user_id
            WHERE c.id = %s
            """,
            (consultation_id,),
        ).fetchone()
        if row is None or (row["user_id"] or "") != user_id:
            raise HTTPException(status_code=404, detail="Consultation not found.")

        case_state = json_object(row["consultation_state"]) or json_object(row["case_state"])

        created_at = row["created_at"]
        return {
            "id": row["id"],
            "case_id": row["case_id"],
            "case_title": row["case_title"],
            "summary": row["summary"],
            "primary_dx": row["primary_dx"],
            "icd_code": row["icd_code"],
            "urgency": row["urgency"],
            "confidence": row["confidence"],
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or ""),
            "case_state": case_state,
        }
    finally:
        con.close()


@router.delete("/api/patient/consultations/{consultation_id}")
async def delete_consultation(
    consultation_id: str,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    from vector_store import get_vector_store

    user_id = cases_user_id(user)
    con = _db.connect()
    try:
        row = con.execute(
            "SELECT user_id FROM consultations WHERE id = %s", (consultation_id,)
        ).fetchone()
        if row is None or (row["user_id"] or "") != user_id:
            raise HTTPException(status_code=404, detail="Consultation not found.")
        con.execute("DELETE FROM consultations WHERE id = %s", (consultation_id,))
        con.commit()
        try:
            get_vector_store().delete(con, consultation_id)
        except Exception as exc:
            log.warning("vector delete failed (ignoring): %s", exc)
        return {"ok": True}
    finally:
        con.close()
