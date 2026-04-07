"""Vercel entrypoint — re-exports the FastAPI ASGI app from main."""

from main import app

__all__ = ["app"]
