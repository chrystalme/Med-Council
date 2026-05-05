"""Database connection factory.

Single source of truth for `DATABASE_URL` parsing and driver selection.

- `postgresql://…`  →  psycopg3 (local Postgres + Cloud SQL). pgvector adapter
  is registered on each connection so `vector` columns round-trip as
  `list[float]` / numpy arrays.
- unset or `sqlite://…`  →  stdlib sqlite3 (legacy fallback for quick bring-up).

Expected local `DATABASE_URL`:
    postgresql://$USER@localhost:5432/medai_council
Expected Cloud SQL (via Cloud Run connector):
    postgresql://medai:$PASSWORD@/medai_council?host=/cloudsql/$INSTANCE

Same call pattern as sqlite3 for compatibility: `con.execute(sql, params)`
returns a cursor you can iterate or call `.fetchone()` / `.fetchall()` on.
Placeholders are `%s` for Postgres — the call sites have been ported.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

log = logging.getLogger("medai.db")

Driver = Literal["postgres", "sqlite"]


def _resolve_driver() -> tuple[Driver, str]:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return "sqlite", str(Path(__file__).resolve().parent / "feedback.db")
    scheme = urlparse(url).scheme.lower()
    if scheme in {"postgres", "postgresql", "postgresql+psycopg"}:
        return "postgres", url
    if scheme == "sqlite":
        return "sqlite", url.replace("sqlite:///", "", 1).replace("sqlite://", "", 1)
    raise RuntimeError(
        f"DATABASE_URL scheme {scheme!r} is not supported. "
        "Use postgresql://… for Postgres or leave unset for the legacy SQLite file."
    )


def get_driver() -> Driver:
    return _resolve_driver()[0]


def connect():
    """Return a new DB connection. Caller is responsible for closing it."""
    driver, target = _resolve_driver()
    if driver == "postgres":
        import psycopg
        from psycopg.rows import dict_row

        con = psycopg.connect(target, row_factory=dict_row, autocommit=False)
        try:
            from pgvector.psycopg import register_vector

            register_vector(con)
        except Exception as exc:
            # pgvector extension may not be installed yet in a fresh DB; ensure_schema
            # will create it. Defer the error to first use of vector columns.
            log.debug("pgvector adapter not registered on this connection: %s", exc)
        return con
    con = sqlite3.connect(target)
    con.row_factory = sqlite3.Row
    return con


__all__ = ["connect", "get_driver", "Driver"]
