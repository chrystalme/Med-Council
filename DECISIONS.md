# Architectural Decisions

A running log of the non-obvious choices in this repo and why they were
taken. New entries go at the bottom with a date; entries are only edited
when a decision is reversed (with a `superseded-by` note), never deleted —
the history is part of the point.

Conventions:

- **Context** — what was true when the decision was made.
- **Decision** — what we chose.
- **Alternatives** — what we considered and rejected.
- **Why** — the load-bearing reason. If this line evaporates, the decision
  should be revisited.
- **Consequences** — what this cost us or locked us in to.

---

## 1. Deploy target: GCP Cloud Run, not Vercel

**Date:** 2026-04-23

**Context.** The repo started on Vercel (Python functions + Next.js on the
same project). Vercel's Python runtime is read-only outside `/tmp`, which
forced a stack of workarounds (SQLite in `/tmp`, no persistent blobs, a
separate `app.py` re-export so the function handler could find the ASGI
app). As the council pipeline grew — long-running LLM calls, per-specialist
streams, PubMed fan-out, attachment blobs — the serverless-function shape
stopped fitting.

**Decision.** Move to GCP Cloud Run for the FastAPI backend, Cloud SQL
(Postgres 15 + pgvector) for state, GCS for blobs, Secret Manager for
secrets, Artifact Registry for images. The Next.js app still ships to
Cloud Run (or Firebase Hosting when we need edge caching).

**Alternatives.**

- **Stay on Vercel** — keep the function-per-route model. Rejected: 60-120s
  timeouts don't leave headroom for 4-6 specialists × ~20s each, and
  Vercel Postgres/KV are deprecated so we'd still need a third-party DB.
- **AWS (ECS/Fargate + RDS + S3)** — more knobs, higher floor on setup
  effort, and no pre-baked pgvector image.
- **Fly.io** — great developer UX but no managed Postgres with pgvector
  and weaker secret management.

**Why.** Cloud Run gives us real Python (no `/tmp` game), scale-to-zero
with ~1-2s cold starts, HTTP/2 streaming, and the Cloud SQL socket mount
at `/cloudsql` — the combination removes every Vercel-shaped workaround
and keeps the operating cost for a side-project near zero during idle.
Cloud SQL ships pgvector enabled, which collapses the vector store onto
the same DB as the rest of the state.

**Consequences.**

- We're on GCP; switching back would mean re-porting the storage + DB layer.
- Migration scripts are now Alembic (`apps/api/alembic/`), run on container
  boot via `docker-entrypoint.sh`. For multi-instance deploys we'll need a
  Cloud Run Job to run migrations ahead of a rollout (see note in `main.py`).

---

## 2. Persistence: Postgres-only, SQLite retired

**Date:** 2026-04-23

**Context.** The old stack stored everything (feedback, cases,
consultations, vector embeddings, attachment blobs) in a single
`feedback.db` SQLite file — chosen at the time because Vercel's FS was
read-only and `/tmp` was the only place a DB could live.

**Decision.** Move every table to Postgres 15, with `pgvector` enabled for
the consultation embedding store. The Python code talks to Postgres via
a single seam: `apps/api/db.py::connect()`.

**Alternatives.**

- **Keep SQLite with a mounted volume on Cloud Run.** Rejected: Cloud Run
  instances are ephemeral and a mounted Cloud Storage FUSE volume has
  unpredictable write latency; concurrent writers would need their own
  locking story.
- **Firestore / Datastore.** Rejected: no SQL, no pgvector equivalent
  without adding Vertex AI Vector Search on top, and the data model is
  genuinely relational (cases → consultations → attachments).
- **Neon / Supabase.** Viable — but given we're on GCP already, Cloud SQL
  keeps networking inside one VPC boundary and avoids a second billing
  relationship.

**Why.** A single store for structured rows and vectors is the lowest-drag
option at our scale (hundreds of users, thousands of consultations). The
seam in `db.py` means swapping Postgres is a single-file change.

**Consequences.**

- Alembic owns the schema now; ad-hoc `CREATE TABLE IF NOT EXISTS` calls
  were deleted from `main.py`. The only way to evolve the schema is a new
  migration in `alembic/versions/`.
- `%s` placeholders and `TIMESTAMPTZ` / `JSONB` types replaced `?` and
  `TEXT` throughout. This makes the code Postgres-specific — callers can't
  be pointed at SQLite again without another port.

---

## 3. Vector store: pgvector, Vertex AI Vector Search stubbed for later

**Date:** 2026-04-23

**Context.** Consultation embeddings are used to surface prior-visit
context to the council on follow-up cases. The original implementation
was a SQLite `BLOB` column + in-process numpy cosine scan — linear in
the user's consultation count, which is fine at tens of rows but falls
apart if a user ever racks up thousands.

**Decision.** Use `pgvector` on the same Postgres instance. Cosine
distance via the `<=>` operator; no ANN index yet (linear scan is
microseconds at current scale). A `VertexVectorStore` stub sits next to
the Postgres implementation so the switch is behind `VECTOR_STORE=vertex`
when we outgrow pgvector.

**Alternatives.**

- **Qdrant / Weaviate / Pinecone.** Rejected for now: extra service,
  extra auth surface, and at our row counts no measurable benefit over
  pgvector.
- **Stay on numpy scans, move blobs to Postgres `bytea`.** Rejected: the
  embedding column wants vector semantics, not bytes — `pgvector`
  gives us correct distance operators for free.

**Why.** pgvector on the same DB means one connection, one backup story,
and the `(user_id, <embedding>)` filter + order-by is one query. We
keep the abstraction so the Vertex migration is a config change when
it eventually pays off.

**Consequences.**

- Postgres must have the `vector` extension. The migration enables it on
  first boot; Cloud SQL supports it in the standard image.
- Adding an ANN index (HNSW / IVFFlat) is a migration when we hit the
  linear-scan ceiling — not urgent.

---

## 4. Attachments: Postgres `bytea` now, GCS behind a storage seam

**Date:** 2026-04-23

**Context.** Case attachments (lab PDFs, pasted text, images) are small
(tens of KB to a few MB) and read once per council run. The old code
stored blobs in SQLite; on Cloud Run, writing ephemeral blobs to the
container FS is worse than no persistence at all.

**Decision.** Phase 1 — store blob bytes in a Postgres `bytea` column
alongside the extracted text and metadata. Phase 2 — move blobs to GCS
via `storage.get_storage()` and keep the metadata row in Postgres. The
storage seam already exists; the GCS implementation is the remaining
stub on the roadmap.

**Alternatives.**

- **Straight to GCS on day one.** Rejected: more moving parts during the
  migration, and signed-URL handling adds UI work we haven't scoped.
- **Inline blobs in the request/response.** Rejected: we need to survive
  page refresh mid-case, so blobs must persist server-side.

**Why.** A Postgres `bytea` column is correct for "small, always read with
its row" data at our scale — one backup story, one transactional write.
The seam lets us graduate to GCS without touching callers when blobs grow.

**Consequences.**

- Postgres row-size grows with attachment count; at ~1 MB per case that's
  fine for thousands of cases. The GCS migration is a hard requirement
  before we take meaningful user load.

---

## 5. Model routing: Groq for free, OpenRouter for everything else

**Date:** 2026-04-23

**Context.** The free-tier default was `nvidia/nemotron-3-super-120b-a12b:free`
on OpenRouter. In practice the `:free` tier has been intermittently
unavailable (429s, queue backups, occasional weeks-long outages) and
Nemotron's clinical reasoning underperformed GPT-OSS-120B in side-by-side
testing.

**Decision.** Wire two model providers at startup — OpenRouter
(`MultiProvider`) and Groq (a thin passthrough `ModelProvider`). Registry
entries whose `id` starts with `groq:` route to the Groq client; everything
else goes through OpenRouter. Default free-tier model is
`groq:openai/gpt-oss-120b`.

**Alternatives.**

- **Single provider (OpenRouter only) + different slug.** Rejected:
  OpenRouter has a `groq/` route but it's rate-limited more aggressively
  than Groq direct, and OpenRouter's free-tier queueing causes user-visible
  slowness.
- **Vertex AI for everything.** Rejected for now: narrower model coverage
  (no Claude, no DeepSeek R1) and higher cost at low volume.

**Why.** Groq direct is the fastest way to serve the free tier (sub-second
first-token on 120B) and it keeps the paid models on OpenRouter where
price-per-token is better than provider-direct. The routing prefix is one
line in the registry; no callers changed.

**Consequences.**

- Two API keys instead of one. `GROQ_API_KEY` is now required for free users
  to hit the default model; a missing key is reported as a structured 503
  with a pointer at the env var.
- We had to write a minimal `_DirectOpenAICompatibleProvider` because
  `MultiProvider` interprets `openai/` prefixes as a routing hint and strips
  them, which would send `gpt-oss-120b` to Groq (404). The passthrough
  forwards the slug verbatim.

---

## 6. OPENAI_API_KEY is tracing-only

**Date:** 2026-04-24

**Context.** The OpenAI Agents SDK exports traces to
platform.openai.com/traces using `OPENAI_API_KEY`. The old speech provider
also read `OPENAI_API_KEY` for Whisper / TTS, which meant one key did two
jobs — and if speech quota was exhausted, tracing failed too.

**Decision.** Reserve `OPENAI_API_KEY` strictly for tracing. Speech reads
`SPEECH_API_KEY` (falling back to `OPENROUTER_API_KEY`), and the inference
path never touches the OpenAI key at all.

**Why.** Separating concerns means a quota event on one path never silences
observability on the others. Tracing is free (or nearly so) on a key with
no billable usage, which further insulates it.

**Consequences.**

- One more env var to set if we ever want a dedicated OpenAI speech key.
- Worth documenting clearly in `apps/api/speech.py` so nobody re-wires it
  "to save an env var."

---

## 7. Speech: OpenAI-compatible provider with configurable base URL

**Date:** 2026-04-23

**Context.** STT (Whisper) and TTS aren't universally available on OpenRouter
(coverage has shifted historically), and we wanted a single implementation
that works across OpenRouter today, Groq Whisper tomorrow, and Google
Cloud Speech when we've fully moved to GCP.

**Decision.** One `OpenAICompatibleSpeechProvider` class that takes a base
URL and API key from env, plus a `GCloudSpeechProvider` branch selected by
`SPEECH_PROVIDER=gcloud`, plus a `disabled` branch that returns 503. Quota
errors (429) raise `SpeechQuotaError` so the API layer can surface a
structured 429 rather than a generic 502.

**Why.** Swapping endpoints (OpenRouter → Groq → OpenAI direct → self-hosted)
is one env var: `SPEECH_BASE_URL`. The `gcloud` branch stays a thin adapter
for when we want to consolidate on Google services.

**Consequences.**

- More env vars (`SPEECH_BASE_URL`, `SPEECH_API_KEY`, `SPEECH_STT_MODEL`,
  `SPEECH_TTS_MODEL`) but every one has a sensible OpenRouter default.
- The `gcloud` path costs a little more code than reusing the OpenAI-
  compatible class, but it's isolated in a single file.

---

## 8. Auth: Clerk, JWT-verified server-side

**Date:** 2026-04-01 _(carried forward from earlier phases)_

**Context.** A medical pipeline needs authenticated users (rate limits,
paywall, per-user patient memory). Building our own auth is a distraction
from the council pipeline.

**Decision.** Clerk handles sign-in / sign-up on the web side; the FastAPI
backend verifies the Clerk JWT on every `/api/*` request via `auth.py`.
Free / Pro entitlement is sourced from Clerk's plan metadata with an API-
side fallback.

**Why.** Clerk's Keyless mode lets a fresh clone boot without any auth
setup, which keeps the first-run story simple. The JWT path keeps the API
stateless — we never hold a session.

**Consequences.**

- We depend on Clerk. A vendor change means re-doing the sign-in UI and the
  JWT verifier in `auth.py` (about a day's work), so not a lock-in we worry
  about.
- Plan-metadata sync has been a recurring source of small bugs (hence the
  fallback path in the `fix(billing): recognise Pro upgrades without
  sign-out` commit).

---

## 9. Rate limiting: in-process sliding window, not Redis

**Date:** 2026-03-20 _(carried forward)_

**Context.** The council pipeline is expensive per request and the free tier
already has upstream rate limits — we still want a cheap first line of
defence against a single user hammering the API.

**Decision.** A sliding-window limiter in-process on each Cloud Run
instance, gated by `RATE_LIMIT_ENABLED`. No Redis.

**Alternatives.**

- **Memorystore (Redis) for cross-instance counters.** Rejected for now: at
  our scale an attacker would have to hit N instances in parallel to evade
  the per-instance limit, which doesn't buy them meaningfully more.

**Why.** Lowest operational complexity. The trade-off (a determined user
can multiply their allowance by the number of live instances) is acceptable
at our current traffic.

**Consequences.**

- When we outgrow this we'll add Memorystore and swap the limiter
  implementation. The limiter is behind its own module so that's a
  focused change.

---

## 10. Frontend: Next.js 16 App Router, `output: "standalone"` for Docker

**Date:** 2026-04-23

**Context.** The web app shipped originally as a Vercel project. Moving to
Cloud Run means we build our own image.

**Decision.** Enable `output: "standalone"` in `next.config.ts` so the
build emits a self-contained server bundle under `.next/standalone/`; the
Dockerfile copies that plus `.next/static` and runs `node server.js`. Same-
origin `/api/*` rewrite keeps the browser inside one origin and matches
what we'll do behind Cloud Run.

**Alternatives.**

- **SSR disabled, static export.** Rejected: we need Clerk server
  components and server actions for the paywall path.
- **Firebase Hosting for the static shell + Cloud Run for APIs.** Viable;
  kept as a fallback if cold-start on the web container becomes an issue.

**Why.** Standalone output is Next.js's first-class answer to "run this
outside Vercel." Cuts the image size dramatically versus shipping
`node_modules`.

**Consequences.**

- Two Dockerfiles now — one per app. Shared base images would be an
  optimisation if/when builds get slow.

---

## 11. Docs live in the repo, not a wiki

**Date:** 2026-04-24

**Context.** Decisions were scattered across commit messages, code
comments, and conversations.

**Decision.** `README.md` for the getting-started + architecture
snapshot; `DECISIONS.md` (this file) for the rationale log;
`terraform/README.md` for deploy-specific runbook detail. Nothing lives
in a wiki.

**Why.** The decisions only stay accurate if they live next to the code
they describe. A wiki drifts; a repo file fails CI when it goes stale.

**Consequences.**

- Every PR that changes a decision is expected to update this file. If
  that becomes friction, we'll add a CI check on changes to the files
  referenced here.
