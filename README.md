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

```bash
open frontend.html
```

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

---

⚠ Demonstration only. AI outputs must not substitute for licensed medical advice.
