"""Case persistence routes — create, list, get, patch.

A case is the long-lived container for a patient's session: title, JSONB
state blob, ownership. Routes are partitioned per user via cases_user_id.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import db as _db
from auth import AuthUser, current_user_maybe_required
from helpers import cases_user_id, utc_now

router = APIRouter()


class CaseCreateIn(BaseModel):
    title: str = Field(default="", max_length=500)


class CasePatchIn(BaseModel):
    state: dict[str, Any]
    title: str | None = Field(default=None, max_length=500)


@router.post("/api/cases")
async def cases_create(req: CaseCreateIn, user: Optional[AuthUser] = Depends(current_user_maybe_required)):
    cid = str(uuid.uuid4())
    uid = cases_user_id(user)
    now = utc_now()
    con = _db.connect()
    con.execute(
        "INSERT INTO cases (id, user_id, title, state, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s)",
        (cid, uid, req.title or "Untitled case", "{}", now, now),
    )
    con.commit()
    con.close()
    return {"id": cid, "title": req.title or "Untitled case", "created_at": now}


@router.get("/api/cases")
def cases_list(user: Optional[AuthUser] = Depends(current_user_maybe_required)):
    uid = cases_user_id(user)
    con = _db.connect()
    rows = con.execute(
        "SELECT id, title, updated_at FROM cases WHERE user_id = %s ORDER BY updated_at DESC LIMIT 50",
        (uid,),
    ).fetchall()
    con.close()
    return {"cases": [{"id": r["id"], "title": r["title"], "updated_at": r["updated_at"]} for r in rows]}


@router.get("/api/cases/{case_id}")
def cases_get(case_id: str, user: Optional[AuthUser] = Depends(current_user_maybe_required)):
    uid = cases_user_id(user)
    con = _db.connect()
    row = con.execute(
        "SELECT id, user_id, title, state, created_at, updated_at FROM cases WHERE id = %s",
        (case_id,),
    ).fetchone()
    con.close()
    if not row or row["user_id"] != uid:
        raise HTTPException(status_code=404, detail="Case not found")
    # JSONB returns a dict via psycopg; string fallback handles legacy sqlite rows.
    raw_state = row["state"]
    if isinstance(raw_state, str):
        try:
            state = json.loads(raw_state or "{}")
        except json.JSONDecodeError:
            state = {}
    else:
        state = raw_state or {}
    return {
        "id": row["id"],
        "title": row["title"],
        "state": state,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@router.patch("/api/cases/{case_id}")
async def cases_patch(
    case_id: str,
    req: CasePatchIn,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    uid = cases_user_id(user)
    con = _db.connect()
    row = con.execute("SELECT user_id FROM cases WHERE id = %s", (case_id,)).fetchone()
    if not row or row["user_id"] != uid:
        con.close()
        raise HTTPException(status_code=404, detail="Case not found")
    now = utc_now()
    state_json = json.dumps(req.state, ensure_ascii=False)
    if req.title is not None:
        con.execute(
            "UPDATE cases SET state = %s, title = %s, updated_at = %s WHERE id = %s",
            (state_json, req.title[:500], now, case_id),
        )
    else:
        con.execute(
            "UPDATE cases SET state = %s, updated_at = %s WHERE id = %s",
            (state_json, now, case_id),
        )
    con.commit()
    con.close()
    return {"id": case_id, "updated_at": now}
