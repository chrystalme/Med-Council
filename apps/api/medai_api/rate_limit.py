"""Optional in-memory rate limiting for POST /api/* (single-instance dev / small deploy)."""

from __future__ import annotations

import os
import time
from collections import deque
from typing import Deque, Dict

from fastapi import HTTPException, Request, status

_WINDOW_SEC = 60
_DEFAULT_MAX = 120  # requests per window per IP when enabled


def rate_limit_enabled() -> bool:
    return os.environ.get("RATE_LIMIT_ENABLED", "").lower() in ("1", "true", "yes")


def rate_limit_max() -> int:
    try:
        return max(1, int(os.environ.get("RATE_LIMIT_MAX_PER_MINUTE", _DEFAULT_MAX)))
    except ValueError:
        return _DEFAULT_MAX


# key -> deque of monotonic timestamps
_buckets: Dict[str, Deque[float]] = {}


def _client_key(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def enforce_rate_limit(request: Request) -> None:
    if not rate_limit_enabled():
        return
    cap = rate_limit_max()
    now = time.monotonic()
    key = _client_key(request)
    dq = _buckets.setdefault(key, deque())
    while dq and now - dq[0] > _WINDOW_SEC:
        dq.popleft()
    if len(dq) >= cap:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Slow down or try again in a minute.",
        )
    dq.append(now)
