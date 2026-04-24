"""One-off migration: copy rows from the legacy `apps/api/feedback.db` SQLite
file into the Postgres database pointed to by DATABASE_URL.

Idempotent — uses ON CONFLICT DO NOTHING keyed on primary keys, so you can
re-run it safely. Skips empty tables automatically. Does NOT migrate the
`vector_embeddings` table (the old SQLite format stored embeddings as raw
numpy bytes; pgvector wants a different representation and we'd have to
re-embed the source text anyway — cheaper to let the next consultation
re-populate it from live data).

Usage:
    cd apps/api
    DATABASE_URL=postgresql://$USER@localhost:5432/medai_council \
        uv run python scripts/migrate_sqlite_to_postgres.py

Or pass an explicit sqlite path:

    uv run python scripts/migrate_sqlite_to_postgres.py \
        --sqlite /path/to/feedback.db
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

import psycopg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--sqlite",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "feedback.db",
        help="Path to the legacy SQLite file (default: apps/api/feedback.db)",
    )
    args = ap.parse_args()

    if not args.sqlite.exists():
        print(f"no sqlite file at {args.sqlite} — nothing to migrate", file=sys.stderr)
        return 1

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1

    sq = sqlite3.connect(str(args.sqlite))
    sq.row_factory = sqlite3.Row

    pg = psycopg.connect(dsn)
    try:
        migrate_feedback(sq, pg)
        migrate_cases(sq, pg)
        migrate_consultations(sq, pg)
        migrate_attachments(sq, pg)
        pg.commit()
    except Exception:
        pg.rollback()
        raise
    finally:
        pg.close()
        sq.close()
    return 0


def _count(con, table: str) -> int:
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def migrate_feedback(sq: sqlite3.Connection, pg) -> None:
    n = _count(sq, "feedback")
    if not n:
        print("feedback: 0 rows — skipping")
        return
    rows = sq.execute(
        "SELECT rating, comment, symptoms, diagnosis, created_at FROM feedback"
    ).fetchall()
    with pg.cursor() as cur:
        for r in rows:
            cur.execute(
                "INSERT INTO feedback (rating, comment, symptoms, diagnosis, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (r["rating"], r["comment"] or "", r["symptoms"] or "", r["diagnosis"] or "", r["created_at"]),
            )
    print(f"feedback: migrated {len(rows)} rows")


def migrate_cases(sq: sqlite3.Connection, pg) -> None:
    n = _count(sq, "cases")
    if not n:
        print("cases: 0 rows — skipping")
        return
    rows = sq.execute(
        "SELECT id, user_id, title, state, created_at, updated_at FROM cases"
    ).fetchall()
    inserted = 0
    with pg.cursor() as cur:
        for r in rows:
            state = r["state"]
            # SQLite stored state as TEXT JSON; normalise to a dict for JSONB.
            if isinstance(state, (bytes, bytearray)):
                state = state.decode("utf-8", errors="replace")
            try:
                state_obj = json.loads(state) if isinstance(state, str) and state.strip() else {}
            except json.JSONDecodeError:
                state_obj = {}
            cur.execute(
                "INSERT INTO cases (id, user_id, title, state, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s::jsonb, %s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (
                    r["id"],
                    r["user_id"] or "",
                    r["title"] or "",
                    json.dumps(state_obj),
                    r["created_at"],
                    r["updated_at"],
                ),
            )
            inserted += cur.rowcount or 0
    print(f"cases: migrated {inserted}/{len(rows)} rows (rest already existed)")


def migrate_consultations(sq: sqlite3.Connection, pg) -> None:
    n = _count(sq, "consultations")
    if not n:
        print("consultations: 0 rows — skipping")
        return
    rows = sq.execute(
        "SELECT id, user_id, case_id, summary, primary_dx, icd_code, urgency, confidence, created_at "
        "FROM consultations"
    ).fetchall()
    inserted = 0
    with pg.cursor() as cur:
        for r in rows:
            cur.execute(
                "INSERT INTO consultations "
                "(id, user_id, case_id, summary, primary_dx, icd_code, urgency, confidence, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (
                    r["id"],
                    r["user_id"],
                    r["case_id"],
                    r["summary"],
                    r["primary_dx"],
                    r["icd_code"],
                    r["urgency"],
                    r["confidence"],
                    r["created_at"],
                ),
            )
            inserted += cur.rowcount or 0
    print(f"consultations: migrated {inserted}/{len(rows)} rows (rest already existed)")


def migrate_attachments(sq: sqlite3.Connection, pg) -> None:
    n = _count(sq, "case_attachments")
    if not n:
        print("case_attachments: 0 rows — skipping")
        return
    rows = sq.execute(
        "SELECT id, case_id, user_id, kind, filename, mime_type, blob, text, size_bytes, "
        "question_index, created_at FROM case_attachments"
    ).fetchall()
    inserted = 0
    with pg.cursor() as cur:
        for r in rows:
            blob = r["blob"]
            if blob is not None and not isinstance(blob, (bytes, bytearray)):
                blob = bytes(blob)
            cur.execute(
                "INSERT INTO case_attachments "
                "(id, case_id, user_id, kind, filename, mime_type, blob, text, size_bytes, "
                " question_index, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (
                    r["id"],
                    r["case_id"],
                    r["user_id"],
                    r["kind"],
                    r["filename"],
                    r["mime_type"],
                    blob,
                    r["text"] or "",
                    r["size_bytes"] or 0,
                    r["question_index"],
                    r["created_at"],
                ),
            )
            inserted += cur.rowcount or 0
    print(f"case_attachments: migrated {inserted}/{len(rows)} rows (rest already existed)")


if __name__ == "__main__":
    sys.exit(main())
