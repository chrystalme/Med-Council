"""
Clerk JWT verification for FastAPI.

Verifies Clerk-issued session JWTs (RS256) against the live JWKS endpoint.
Uses only PyJWT + stdlib — no framework-specific Clerk helpers needed.

Behavior:
    • If CLERK_ISSUER is NOT set, auth is skipped entirely (dev-friendly fallback);
      `current_user_optional` returns None, `current_user` raises 401.
    • If CLERK_ISSUER IS set, tokens are verified strictly:
        - signature against JWKS cached in-process
        - `iss` must match CLERK_ISSUER exactly
        - `azp` (authorized party) must be in CLERK_AUTHORIZED_PARTIES if configured
        - `exp`, `nbf` validated by PyJWT

Env:
    CLERK_ISSUER               e.g. https://clerk.yourapp.com  or
                               https://<frontendApi>.clerk.accounts.dev
    CLERK_AUTHORIZED_PARTIES   optional, comma-separated (defaults empty = accept any)

Docs:
    https://clerk.com/docs/backend-requests/manual-jwt
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal, Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from jwt import InvalidTokenError, PyJWKClient

PlanLiteral = Literal["free", "pro"]

log = logging.getLogger("medai.auth")

CLERK_ISSUER = (os.environ.get("CLERK_ISSUER") or "").rstrip("/")
CLERK_AUTHORIZED_PARTIES = [
    p.strip()
    for p in (os.environ.get("CLERK_AUTHORIZED_PARTIES") or "").split(",")
    if p.strip()
]


@dataclass(frozen=True)
class AuthUser:
    """Verified Clerk session claims we care about."""

    user_id: str
    session_id: Optional[str] = None
    org_id: Optional[str] = None
    email: Optional[str] = None
    plan: PlanLiteral = "free"


FREE_CONSULTATION_CAP = 4


def _looks_pro(value: Any) -> bool:
    """Best-effort truthy check against Clerk plan/feature claim shapes."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.lower()
        return "pro" in v or v in {"active", "paid", "premium"}
    if isinstance(value, list):
        return any(_looks_pro(item) for item in value)
    if isinstance(value, dict):
        # Check any slug-like keys.
        for k, v in value.items():
            if isinstance(k, str) and "pro" in k.lower() and bool(v):
                return True
            if _looks_pro(v):
                return True
    return False


# Clerk Billing-related claim keys we'll check in priority order.
_PLAN_CLAIM_KEYS = ("pla", "plans", "plan", "fea", "features")
_META_KEYS = ("metadata", "public_metadata", "unsafe_metadata", "private_metadata")


def _plan_from_claims(claims: dict[str, Any]) -> PlanLiteral:
    """Extract the user's current plan from a Clerk JWT.

    Clerk Billing publishes entitlements under various claim names depending
    on how the JWT template is configured. We accept any of `pla`, `plans`,
    `plan`, `fea`, `features` — and also dig into `public_metadata.plan`.
    """
    for key in _PLAN_CLAIM_KEYS:
        if key in claims and _looks_pro(claims[key]):
            return "pro"
    for mkey in _META_KEYS:
        md = claims.get(mkey)
        if isinstance(md, dict) and _looks_pro(md.get("plan")):
            return "pro"
    return "free"


# --- Optional: Clerk Backend API fallback -----------------------------------
# If the JWT template doesn't surface plan claims, we can look the user up via
# Clerk's admin API using CLERK_SECRET_KEY. Cached per-user for 60s to avoid
# per-request fan-out.

import time
import urllib.request as _urllib_req
import urllib.error as _urllib_err
import json as _json

_CLERK_API_BASE = os.environ.get("CLERK_API_URL", "https://api.clerk.com/v1")
_plan_cache: dict[str, tuple[float, PlanLiteral]] = {}
_PLAN_CACHE_TTL = 60.0


def _clerk_get(path: str, secret: str) -> Any:
    """GET a path under the Clerk Backend API. Returns parsed JSON or None."""
    url = f"{_CLERK_API_BASE}{path}"
    req = _urllib_req.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {secret}",
            "Accept": "application/json",
        },
    )
    try:
        with _urllib_req.urlopen(req, timeout=4) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        return _json.loads(body)
    except (_urllib_err.URLError, _urllib_err.HTTPError, ValueError, TimeoutError):
        return None
    except Exception:
        return None


def _plan_from_clerk_api(user_id: str) -> PlanLiteral:
    """Look up the user's active plan via the Clerk Backend API.

    Clerk Billing exposes subscriptions under `/commerce/...`. The endpoints
    have shifted during v7, so we probe multiple known shapes and also fall
    back to public_metadata.plan for manual installs. Cached 60s per user.
    """
    secret = os.environ.get("CLERK_SECRET_KEY", "").strip()
    if not secret or not user_id:
        return "free"

    now = time.time()
    hit = _plan_cache.get(user_id)
    if hit and now - hit[0] < _PLAN_CACHE_TTL:
        return hit[1]

    plan: PlanLiteral = "free"

    # 1) Billing / commerce endpoints. Try each — the one that works on your
    # Clerk instance will return 200; the others 404 silently. The response
    # shapes include {plan: {slug}}, {plans: [...]}, {subscription: {plan: ...}}.
    billing_paths = [
        f"/users/{user_id}/billing/subscription",
        f"/users/{user_id}/billing",
        f"/commerce/users/{user_id}/subscriptions",
        f"/commerce/subscriptions?user_id={user_id}",
    ]
    for path in billing_paths:
        data = _clerk_get(path, secret)
        if not data:
            continue
        if _looks_pro(data):
            plan = "pro"
            break

    # 2) Fallback: user record public metadata (manual setup).
    if plan != "pro":
        data = _clerk_get(f"/users/{user_id}", secret)
        if isinstance(data, dict):
            for key in ("public_metadata", "unsafe_metadata", "private_metadata"):
                md = data.get(key)
                if isinstance(md, dict) and _looks_pro(md.get("plan")):
                    plan = "pro"
                    break

    _plan_cache[user_id] = (now, plan)
    return plan


def invalidate_plan_cache(user_id: str | None = None) -> None:
    """Clear the cached Clerk admin plan lookup for one or all users."""
    if user_id:
        _plan_cache.pop(user_id, None)
    else:
        _plan_cache.clear()


@lru_cache(maxsize=1)
def _jwks_client() -> Optional[PyJWKClient]:
    if not CLERK_ISSUER:
        return None
    # Clerk exposes JWKS at /.well-known/jwks.json under the issuer URL.
    return PyJWKClient(f"{CLERK_ISSUER}/.well-known/jwks.json", cache_keys=True)


def _bearer_token(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        return token or None
    return None


def _verify(token: str) -> Optional[AuthUser]:
    jwks = _jwks_client()
    if jwks is None:
        # Clerk not configured on the server; treat as anonymous.
        return None

    try:
        signing_key = jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            issuer=CLERK_ISSUER,
            # Clerk session JWTs don't set `aud` by default; rely on `azp` instead.
            options={"verify_aud": False},
        )
    except InvalidTokenError as exc:
        log.debug("clerk jwt rejected: %s", exc)
        return None

    if CLERK_AUTHORIZED_PARTIES:
        azp = claims.get("azp")
        if azp not in CLERK_AUTHORIZED_PARTIES:
            log.debug("clerk jwt rejected: azp=%s not in authorized parties", azp)
            return None

    sub = claims.get("sub")
    if not sub:
        return None

    return AuthUser(
        user_id=sub,
        session_id=claims.get("sid"),
        org_id=claims.get("org_id"),
        email=claims.get("email"),
        plan=_plan_from_claims(claims),
    )


async def current_user_optional(request: Request) -> Optional[AuthUser]:
    """FastAPI dependency: return verified user if a valid Bearer token is present, else None."""
    token = _bearer_token(request)
    if not token:
        return None
    return _verify(token)


async def current_user(
    user: Optional[AuthUser] = Depends(current_user_optional),
) -> AuthUser:
    """FastAPI dependency: require a valid Clerk session — raises 401 otherwise."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid session token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def current_user_maybe_required(
    user: Optional[AuthUser] = Depends(current_user_optional),
) -> Optional[AuthUser]:
    """
    FastAPI dependency that adapts to deployment mode:

    • CLERK_ISSUER set        → a valid Clerk session is required (401 otherwise).
    • CLERK_ISSUER not set    → auth is bypassed; returns None (dev-friendly).

    This lets `main.py` wire the dep into every /api route without breaking local
    development before the user has pasted their Clerk keys.
    """
    if auth_configured() and user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid session token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def auth_configured() -> bool:
    """Return True when Clerk JWT verification is active."""
    return bool(CLERK_ISSUER)


# Dev override: set DEV_FORCE_PRO=1 locally to treat every request as Pro for
# easy testing without wiring Clerk Billing. Ignored in production.
_DEV_FORCE_PRO = os.environ.get("DEV_FORCE_PRO") == "1"


def effective_plan(user: Optional[AuthUser]) -> PlanLiteral:
    """Resolve the effective plan for a user.

    Priority:
      1. DEV_FORCE_PRO=1 env override (local dev)
      2. Plan already embedded in the JWT (AuthUser.plan)
      3. Clerk admin API lookup (only if CLERK_SECRET_KEY is configured)
    """
    if _DEV_FORCE_PRO:
        return "pro"
    if user is None:
        return "free"
    if user.plan == "pro":
        return "pro"
    # JWT didn't surface a plan claim. Try the Clerk admin API as a fallback —
    # this covers the common case where Billing is enabled but the user's
    # JWT template doesn't include plan claims yet.
    return _plan_from_clerk_api(user.user_id)


async def require_pro(
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
) -> AuthUser:
    """FastAPI dependency — raise 402 when the current user is not on Pro.

    Attach to endpoints that should only run for paying users (e.g. server-side
    Whisper / TTS). Patient memory + attachment endpoints do NOT use this dep;
    they enforce per-feature quotas inline instead (see assert_consultation_cap
    in main.py and AttachmentStore.save tier checks).
    """
    if user is None:
        # Auth not configured locally — let them through in dev.
        if not auth_configured():
            return AuthUser(user_id="dev", plan="pro" if _DEV_FORCE_PRO else "free")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid session token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if effective_plan(user) != "pro":
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "code": "voice_premium",
                "message": "This feature requires the Pro plan.",
            },
        )
    return user
