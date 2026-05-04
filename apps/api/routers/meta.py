"""Meta routes — health, service info, agent inventory, current-user, model allowlist.

These routes have no DB or agent dependencies; they only read module-level
config (registries, env vars, JWT claims). First batch of the route split
out of main.py.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse

from auth import AuthUser, current_user_maybe_required
from council import MODEL, SPECIALIST_META
from council_registry import DEFAULT_MODEL_KEY, models_for_plan

router = APIRouter()


@router.get("/")
async def serve_root():
    """Root of the API service.

    In the GCP deploy the UI lives on a separate Cloud Run service, so `/` here
    redirects the browser to `WEB_BASE_URL` when that env var is set (which it
    is on prod). Without it — local dev, ad-hoc curl — we return a minimal
    JSON pointer instead of the legacy `static/index.html`, which was the old
    Vercel-era UI and is retired.
    """
    # `Cache-Control: no-store` prevents browsers from caching the old
    # pre-redirect HTML body (saw that cause "old UI still showing" reports
    # after the retirement of static/index.html).
    no_cache_headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    web_url = os.environ.get("WEB_BASE_URL", "").strip()
    if web_url:
        return RedirectResponse(web_url, status_code=302, headers=no_cache_headers)
    return JSONResponse(
        {
            "service": "MedAI Council API",
            "docs": "/docs",
            "health": "/health",
            "ui": "UI lives on a separate Cloud Run service — set WEB_BASE_URL on this container to auto-redirect.",
        },
        headers=no_cache_headers,
    )


@router.get("/index.html", include_in_schema=False)
async def serve_index_alias():
    return RedirectResponse("/", status_code=307)


@router.get("/health")
def health():
    return {
        "status": "ok",
        "service": "MedAI Council",
        "version": "3.0.0",
        "model": MODEL,
        "inference": "openrouter",
    }


@router.get("/specialists")
def list_specialists():
    return {
        "specialists": [{"id": sid, **meta} for sid, meta in SPECIALIST_META.items()]
    }


@router.get("/agents")
def list_agents():
    """List all available physicians/agents for the council"""
    return {
        "physicians": [
            {
                "id": sid,
                "name": meta["name"],
                "specialty": meta["specialty"],
                "initials": meta["initials"],
            }
            for sid, meta in SPECIALIST_META.items()
        ]
    }


@router.get("/api/me")
async def me(
    request: Request,
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
    debug: int = 0,
    refresh: int = 0,
):
    """Return the resolved plan + basic profile for the current session.

    Query params:
      ?debug=1   — also return the raw JWT claims + admin-API result so you
                   can see exactly what Clerk is sending.
      ?refresh=1 — bust the Clerk admin-API plan cache for this user (useful
                   immediately after subscribing so you don't have to wait
                   up to 60s for the cache to expire).
    """
    from auth import (
        _plan_from_claims,
        _plan_from_clerk_api,
        auth_configured,
        effective_plan,
        invalidate_plan_cache,
    )

    if refresh and user:
        invalidate_plan_cache(user.user_id)

    plan = effective_plan(user)
    payload: dict[str, Any] = {
        "user_id": user.user_id if user else None,
        "email": user.email if user else None,
        "plan": plan,
    }

    if debug:
        # Decode the JWT without verifying — useful when CLERK_ISSUER is unset
        # or verification fails, so you can still read iss / pla / fea.
        import jwt as _jwt

        token = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
        raw_claims: dict[str, Any] = {}
        if token:
            try:
                raw_claims = _jwt.decode(token, options={"verify_signature": False})
            except Exception as exc:
                raw_claims = {"__decode_error__": str(exc)}

        unverified_plan: str | None = None
        if isinstance(raw_claims, dict) and "__decode_error__" not in raw_claims:
            unverified_plan = _plan_from_claims(raw_claims)

        dbg: dict[str, Any] = {
            "jwt_plan_from_claims": user.plan if user else None,
            "clerk_api_plan": _plan_from_clerk_api(user.user_id) if user else "free",
            "raw_claims": raw_claims,
            "clerk_jwt_verification_enabled": auth_configured(),
            "plan_from_unverified_jwt_claims": unverified_plan,
        }
        if token and user is None and not auth_configured():
            iss = raw_claims.get("iss") if isinstance(raw_claims, dict) else None
            dbg["fix_hint"] = (
                "FastAPI is not verifying Clerk JWTs (CLERK_ISSUER unset in apps/api/.env). "
                "Every request is anonymous — resolved plan stays free even though the browser token has Pro claims. "
                f"Set CLERK_ISSUER to your session JWT iss (e.g. {iss!r})."
            )
        payload["debug"] = dbg

    return payload


@router.get("/api/models")
async def list_models(
    user: Optional[AuthUser] = Depends(current_user_maybe_required),
):
    """Return the model allowlist visible to this user, with `locked` flags for
    Pro-only entries when the caller is on the Free tier. The frontend dropdown
    uses this to render the picker.
    """
    from auth import effective_plan

    plan = effective_plan(user)
    return {
        "default": DEFAULT_MODEL_KEY,
        "plan": plan,
        "models": models_for_plan(plan),
    }
