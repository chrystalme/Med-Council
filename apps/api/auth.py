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
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from jwt import InvalidTokenError, PyJWKClient

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
