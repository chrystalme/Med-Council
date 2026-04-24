# syntax=docker/dockerfile:1.7

# ── deps: install exact versions from the workspace lockfile ─────────────────
FROM node:22-slim AS deps

RUN corepack enable && corepack prepare pnpm@9 --activate

WORKDIR /repo

# Copy workspace manifests so pnpm only needs to fetch once and we can leverage
# the filter to install just the web app's deps + hoisted tooling.
COPY pnpm-workspace.yaml pnpm-lock.yaml package.json ./
COPY apps/web/package.json apps/web/package.json

RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile --filter web...


# ── builder: produce the standalone Next.js server ──────────────────────────
FROM node:22-slim AS builder

RUN corepack enable && corepack prepare pnpm@9 --activate

WORKDIR /repo
COPY --from=deps /repo/node_modules ./node_modules
COPY --from=deps /repo/apps/web/node_modules ./apps/web/node_modules
COPY pnpm-workspace.yaml pnpm-lock.yaml package.json ./
COPY apps/web ./apps/web

ENV NEXT_TELEMETRY_DISABLED=1

# Public env baked into the client bundle. For compose, the browser hits the
# Next rewrite proxy so the host is irrelevant at build time — but this
# variable is referenced during `next build`, so provide a sane default.
ARG NEXT_PUBLIC_API_BASE_URL=/api
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}

# Server-side API origin for the Next rewrite. Next.js evaluates rewrites()
# at build time in standalone output, so this MUST be present at build, not
# only at runtime on Cloud Run. Default is the local API for compose; in prod
# pass the deployed Cloud Run API URL via --build-arg.
ARG API_BASE_URL=http://localhost:8000
ENV API_BASE_URL=${API_BASE_URL}

# Clerk publishable key is read by ClerkProvider in the client bundle, which
# means Next.js inlines it at build time — it must be present here, not just
# at runtime. Publishable keys are public by design (visible in every rendered
# HTML page), so baking into the image is safe.
ARG NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
ENV NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=${NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY}

RUN pnpm --filter web build


# ── runtime: tiny image with just the standalone server ─────────────────────
FROM node:22-slim AS runtime

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN apt-get update && \
    apt-get install -y --no-install-recommends tini ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd --system app && useradd --system --gid app --home /app app

WORKDIR /app

# Standalone output packages only the runtime deps + server.js entrypoint.
COPY --from=builder --chown=app:app /repo/apps/web/.next/standalone ./
COPY --from=builder --chown=app:app /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder --chown=app:app /repo/apps/web/public ./apps/web/public

USER app
EXPOSE 3000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["node", "apps/web/server.js"]
