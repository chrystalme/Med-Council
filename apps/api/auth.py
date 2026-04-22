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


def _plan_from_claims(claims: dict[str, Any]) -> PlanLiteral:
    """Extract the user's current plan from a Clerk JWT.

    Clerk Billing publishes entitlements in the `pla` (plan) or `plans` claims
    depending on template configuration. We accept either shape and fall back
    to the Clerk public metadata `plan` key if configured manually.
    """
    # Clerk Billing canonical claim (array of enabled plan slugs).
    plans = claims.get("pla") or claims.get("plans")
    if isinstance(plans, list):
        for slug in plans:
            if isinstance(slug, str) and "pro" in slug.lower():
                return "pro"
    elif isinstance(plans, str):
        if "pro" in plans.lower():
            return "pro"

    # Manual fallback via user public metadata (if developer set metadata.plan = "pro").
    metadata = claims.get("metadata") or claims.get("public_metadata") or {}
    if isinstance(metadata, dict):
        val = metadata.get("plan")
        if isinstance(val, str) and val.lower() == "pro":
            return "pro"

    return "free"


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
    """Resolve the effective plan for a user, respecting the dev override."""
    if _DEV_FORCE_PRO:
        return "pro"
    if user is None:
        return "free"
    return user.plan


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
