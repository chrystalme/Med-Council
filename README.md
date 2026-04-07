# MedAI Council v3

**Inference: OpenRouter → nvidia/nemotron-3-super-120b-a12b**  
**Tracing: OpenAI platform → platform.openai.com/traces**

## What changed from v2

| | v2 | v3 |
|---|---|---|
| Inference provider | OpenAI | **OpenRouter** |
| Model | gpt-4o | **nvidia/nemotron-3-super-120b-a12b** |
| Client setup | default | `set_default_openai_client()` + `set_default_openai_api("chat_completions")` |
| Structured output | `output_type=` (OpenAI native) | JSON-in-prompt + `parse_json()` fallback |
| Tracing key | OPENAI_API_KEY | **OPENAI_API_KEY** (tracing only, separate from inference) |
| Inference key | OPENAI_API_KEY | **OPENROUTER_API_KEY** |

## About the model

**NVIDIA Nemotron 3 Super** (`nvidia/nemotron-3-super-120b-a12b`)
- 120B parameters, 12B active (hybrid MoE architecture)
- Built specifically for multi-agent applications
- 1M token context window
- Hybrid Mamba-Transformer with multi-token prediction
- Available as paid (`nvidia/nemotron-3-super-120b-a12b`) or free-tier (`:free` suffix)

To use the free tier, change `MODEL` in `council.py`:
```python
MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
```

## Quick start

### 1 — Install

```bash
pip install -r requirements.txt
```

### 2 — Set environment variables

```bash
# Model inference via OpenRouter
export OPENROUTER_API_KEY=sk-or-...

# Tracing to platform.openai.com/traces (separate key)
export OPENAI_API_KEY=sk-...
```

Windows (PowerShell):
```powershell
$env:OPENROUTER_API_KEY = "sk-or-..."
$env:OPENAI_API_KEY     = "sk-..."
```

### 3 — Start backend

```bash
uvicorn main:app --reload --port 8000
```

On startup:
```
✓ Inference  → OpenRouter  (nvidia/nemotron-3-super-120b-a12b)
✓ Tracing    → platform.openai.com/traces
```

### 4 — Open frontend

With **`uvicorn main:app --port 8000`**, open **`http://127.0.0.1:8000/`** (the UI is served by FastAPI from `static/index.html`).

Alternatively open `static/index.html` from disk (`file://`); it still calls `http://localhost:8000` for the API. Or run **`vercel dev`** for the same origin as production.

## Deploy to Vercel

1. Connect this repository in the [Vercel dashboard](https://vercel.com/new) or deploy from the CLI (`npm i -g vercel` then `vercel` / `vercel --prod` in the project root). See [FastAPI on Vercel](https://vercel.com/docs/frameworks/backend/fastapi).

2. Set **Environment Variables** on the project: `OPENROUTER_API_KEY`, `OPENAI_API_KEY` (same as local `.env`).

3. **Entrypoint:** root [`app.py`](./app.py) re-exports the FastAPI `app` from [`main.py`](./main.py). **UI:** [`static/index.html`](./static/index.html) is returned by **`GET /`** from the same serverless function as the API (so routing stays consistent). Use your deployment URL root (e.g. `https://your-project.vercel.app/`), not a separate static path.

4. Agent runs can be long; watch function duration and logs if requests time out ([functions limits](https://vercel.com/docs/functions/limitations)).

### Troubleshooting (Vercel)

- **`{"detail":"Not Found"}` in the app** — In the browser **Network** tab, confirm failing requests go to **your** deployment host (same tab URL), use relative paths (`/health`, `/api/…`), and return **200** for `GET /health`. If previews use **Deployment Protection** ([docs](https://vercel.com/docs/deployment-protection)), unauthenticated API calls can fail; use **relative** `fetch` URLs and `credentials: 'include'` (already set in the UI), or turn protection off for the environment you are testing.

- **Cookie `_vercel_sso_nonce` rejected (cross-site / SameSite)** — Usually from **Vercel Authentication** or viewing the deployment in a **cross-site** context. It does not mean your FastAPI code set that cookie. Testing in a normal top-level window on the deployment URL avoids most noise; you can ignore the warning if the app works.

## Files changed from v2

Only three files changed. `frontend.html` is **identical** to v2.

```
council.py   — MODEL constant changed to nvidia/..., output_type= removed,
               JSON schemas embedded in agent instructions instead
main.py      — set_default_openai_client() with OpenRouter base_url,
               set_default_openai_api("chat_completions"),
               two env vars (OPENROUTER_API_KEY + OPENAI_API_KEY),
               parse_json() helper handles model's raw text output
requirements.txt — added explicit openai>=1.57.0
```

## How OpenRouter + openai-agents works

```python
from openai import AsyncOpenAI
from agents import set_default_openai_client, set_default_openai_api

client = AsyncOpenAI(
    api_key=os.environ["OPENROUTER_API_KEY"],
    base_url="https://openrouter.ai/api/v1",
)
set_default_openai_client(client)          # all Agent() instances use this client
set_default_openai_api("chat_completions") # required: use /chat/completions not /responses
```

`set_tracing_api_key()` takes a **separate** OpenAI key and sends trace data to
`platform.openai.com/traces` regardless of which provider handles inference.

## Pipeline walkthrough (GIF)

An animated overview of UI stages and API routes is at **`docs/medai-council-pipeline.gif`** (~8 frames, ~3.2s each).

**Demo UI walkthrough (hypothetical patient):** `docs/medai-council-demo-ui.gif` — mock screens in the app’s dark theme with synthetic symptoms, follow-ups, council, research, diagnosis, plan, and message + follow-up panel.

Regenerate (requires Pillow):

```bash
uv pip install pillow --python .venv/bin/python
.venv/bin/python scripts/generate_pipeline_gif.py
.venv/bin/python scripts/generate_demo_ui_gif.py
```

---

⚠ Demonstration only. AI outputs must not substitute for licensed medical advice.
