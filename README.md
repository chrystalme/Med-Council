# MedAI Council

A multi-specialty clinical deliberation system. Sixteen AI specialists
deliberate on a patient case in parallel, consult the literature, converge
on a diagnosis, and return a plan plus a message the patient can act on.

**Status:** research artefact · demonstration only · not a substitute for
licensed medical advice.

---

## Repository layout

```
medai-council/
├── apps/
│   ├── api/            FastAPI backend — the actual council pipeline
│   │   ├── main.py     ASGI entrypoint, routes, startup
│   │   ├── council.py  Specialist Agent definitions (openai-agents SDK)
│   │   ├── council_*.py  Registry, schemas, tools, handoffs
│   │   ├── static/     Legacy vanilla UI (served at /, being phased out)
│   │   └── requirements.txt
│   └── web/            Next.js 16 frontend (App Router + Clerk + Tailwind v4)
├── pnpm-workspace.yaml
├── package.json        Monorepo root — pnpm workspace scripts
└── README.md
```

Two services, two deploy targets — eventually both on GCP Cloud Run.

---

## Prerequisites

- **Node ≥ 20** and **pnpm ≥ 9** (for the web app)
- **Python 3.12** (for the API — see `apps/api/.python-version`)
- Accounts:
  - [OpenRouter](https://openrouter.ai) — model inference
  - [OpenAI](https://platform.openai.com) — tracing only (free)
  - [Clerk](https://dashboard.clerk.com) — authentication
  - *Later:* Neon, Resend, GCP

---

## First-time setup

### 1. Install web dependencies

```bash
pnpm install
```

### 2. Install API dependencies

```bash
cd apps/api
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure environment

Copy both example files and fill in values:

```bash
cp apps/api/.env.example apps/api/.env
cp apps/web/.env.example apps/web/.env.local
```

Minimum required to run the API:

- `OPENROUTER_API_KEY` · [openrouter.ai/keys](https://openrouter.ai/keys)
- `OPENAI_API_KEY` · [platform.openai.com/api-keys](https://platform.openai.com/api-keys) (tracing only)

The web app can boot in **Clerk Keyless mode** without any Clerk keys
— Clerk auto-provisions temporary dev keys the first time it loads,
and shows a "Claim your application" prompt you can click to attach
your own Clerk account later.

---

## Development

Run the two services in separate terminals.

### Frontend (Next.js on :3000)

```bash
pnpm dev
```

### Backend (FastAPI on :8000)

```bash
cd apps/api
source .venv/bin/activate
uvicorn main:app --reload --port 8000
```

The frontend calls the backend via `NEXT_PUBLIC_API_BASE_URL` (default
`http://localhost:8000`). Clerk-protected routes live under `/case`.

---

## The pipeline (seven stages)

| № | Stage | Endpoint | What it does |
|---|---|---|---|
| I | Intake | `POST /api/intake/followup` | Generates four clarifying questions |
| II | Triage | `POST /api/triage` | Selects 4–6 specialists for deliberation |
| III | Council | `POST /api/council/physician` | Per-specialist assessment, one call each |
| IV | Research | `POST /api/research` | PubMed evidence round-up |
| V | Consensus | `POST /api/consensus` | Structured diagnosis, ICD, confidence, urgency |
| VI | Plan | `POST /api/plan` | Cross-specialty treatment plan |
| VII | Message | `POST /api/message` | Empathetic patient-facing summary |

A follow-up Q&A loop is available at `POST /api/message/followup`.

---

## Roadmap

- [x] **Step 1** — commit current state to `main`
- [ ] **Step 2a** — monorepo restructure + Next.js scaffold + Clerk auth gate *(in progress)*
- [ ] **Step 2b–d** — port the seven stages to Next.js
- [ ] **Step 3** — Postgres persistence (case history, returning-patient continuity)
- [ ] **Step 4** — on-call doctor escalation via Resend on high-severity cases
- [ ] **Step 5** — SSE streaming, parallel specialist fan-out, rate limiting,
      red-flag rules, tests, split routes
- [ ] **Step 6** — paid tiers via Clerk/Stripe (free LLM for now)
- [ ] **Step 7** — GCP migration (Cloud Run + Cloud SQL)

---

## Model inference

Currently routed through **OpenRouter → `nvidia/nemotron-3-super-120b-a12b:free`**
via the `openai-agents` SDK. Free tier has ~20 req/min and ~50-1000/day
limits; the full council of 16 specialists is selective (4–6 per case) so
this stays inside the budget for development.

Swap to the paid variant by editing `MODEL` in
`apps/api/council_registry.py`.

---

## Tracing

All agent runs export to [platform.openai.com/traces](https://platform.openai.com/traces)
via the Agents SDK's built-in tracer. Uses `OPENAI_API_KEY` — separate from
the inference key.

---

⚠ Demonstration only. AI outputs must not substitute for licensed medical advice.
