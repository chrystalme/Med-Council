from __future__ import annotations

import logging
import os
import sys
from contextlib import contextmanager
from typing import Any, Iterator

log = logging.getLogger("medai.langfuse")

_client: Any | None = None
_instrumented = False


def _enabled() -> bool:
    return (os.environ.get("LANGFUSE_ENABLED") or "1").lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def langfuse_base_url() -> str:
    base_url = (os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or "").strip()
    if base_url:
        os.environ["LANGFUSE_BASE_URL"] = base_url
    return base_url


def langfuse_configured() -> bool:
    return bool(
        _enabled()
        and os.environ.get("LANGFUSE_PUBLIC_KEY")
        and os.environ.get("LANGFUSE_SECRET_KEY")
        and langfuse_base_url()
    )


def configure_langfuse() -> bool:
    """Instrument OpenAI Agents SDK traces with Langfuse when credentials exist."""
    global _client, _instrumented

    if not langfuse_configured():
        log.info("Langfuse tracing disabled; set LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, and LANGFUSE_BASE_URL.")
        return False

    try:
        if not _instrumented:
            from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

            OpenAIAgentsInstrumentor().instrument()
            _instrumented = True

        from langfuse import get_client

        _client = get_client()
        log.info("Langfuse tracing enabled: %s", langfuse_base_url())
        return True
    except Exception as exc:
        log.warning("Langfuse tracing failed to initialise: %s", exc)
        _client = None
        return False


def flush_langfuse() -> None:
    if _client is None:
        return
    try:
        _client.flush()
    except Exception as exc:
        log.debug("Langfuse flush failed: %s", exc)


@contextmanager
def langfuse_attributes(
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> Iterator[None]:
    """Propagate high-level trace attributes to Langfuse observations."""
    if _client is None:
        yield
        return

    cm = None
    try:
        from langfuse import propagate_attributes

        cm = propagate_attributes(
            user_id=user_id,
            session_id=session_id,
            metadata=metadata,
            tags=tags,
            version=os.environ.get("GIT_SHA") or os.environ.get("K_REVISION"),
        )
        cm.__enter__()
    except Exception as exc:
        log.debug("Langfuse attribute propagation unavailable: %s", exc)
        yield
        return

    try:
        yield
    except BaseException:
        exc_info = sys.exc_info()
        if cm is not None and cm.__exit__(*exc_info):
            return
        raise
    else:
        if cm is not None:
            cm.__exit__(None, None, None)
