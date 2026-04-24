# syntax=docker/dockerfile:1.7

# ── builder: resolve + install deps with uv into a standalone venv ───────────
FROM python:3.12-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    PYTHONDONTWRITEBYTECODE=1

# uv is distributed as a static binary via its official image — copy it in.
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/

WORKDIR /app

# Install deps first (cached layer). Only project files change on most builds.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Now copy the source and install the project itself.
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# ── runtime: slim image, no uv, no build toolchain ───────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Minimal OS-level libs psycopg[binary] + google-cloud-storage need at runtime.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates tini && \
    rm -rf /var/lib/apt/lists/*

# Non-root runtime user (Cloud Run ignores USER but other hosts respect it).
RUN groupadd --system app && useradd --system --gid app --home /app app

WORKDIR /app
COPY --from=builder --chown=app:app /app /app

USER app

# Cloud Run injects PORT=8080; fall back for local docker runs.
ENV PORT=8080
EXPOSE 8080

# Entrypoint runs Alembic migrations then execs uvicorn. tini handles signals.
ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker-entrypoint.sh"]
