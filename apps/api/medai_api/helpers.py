"""Shared route-level helpers.

Intentionally tiny — exists to break the would-be circular import between
main.py and routers/* once routes start moving out. Anything broader than
"two-line utility used by multiple routers" should live in its own module.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from .auth import AuthUser


def utc_now() -> str:
    """ISO-8601 UTC timestamp string used as created_at / updated_at."""
    return datetime.now(timezone.utc).isoformat()


def cases_user_id(user: Optional[AuthUser]) -> str:
    """Resolve the user_id used as the partition key for /api/cases-scoped data.

    Anonymous (auth-disabled) callers map to the empty string so dev-mode
    rows store under a stable key. With Clerk verification enabled, this
    returns the JWT subject.
    """
    return user.user_id if user else ""


def json_object(raw: Any) -> dict[str, Any]:
    """Coerce a JSONB-or-TEXT value to a dict.

    Postgres JSONB returns a dict via psycopg; legacy SQLite rows return
    a string. Returns an empty dict on parse failure or other types so
    callers don't have to branch.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
