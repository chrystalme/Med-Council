"""
MedAI Council — shared registry (specialists, model allowlist, prompt fragments).
"""

from __future__ import annotations

import os
from typing import Literal, TypedDict


class ModelEntry(TypedDict):
    id: str
    label: str
    tier: Literal["free", "pro"]
    description: str


# Curated allowlist. Keys are stable identifiers the frontend sends; `id` is the
# slug routed by main.py's startup providers.
#
# Routing convention on `id`:
#   - `vertex:<model>` → Vertex AI's OpenAI-compat endpoint (all models in-house)
#   - anything else    → OpenRouter (currently only `openai/gpt-5`)
# The prefix is stripped before the slug is handed to the downstream client.
MODELS: dict[str, ModelEntry] = {
    "gemini-2-5-flash-lite-free": {
        "id": "vertex:google/gemini-2.5-flash-lite",
        "label": "Gemini 2.5 Flash Lite",
        "tier": "free",
        "description": "Vertex AI · fastest, cheapest Gemini for the free tier",
    },
    "gemini-2-5-pro": {
        "id": "vertex:google/gemini-2.5-pro",
        "label": "Gemini 2.5 Pro",
        "tier": "pro",
        "description": "Vertex AI · 1M-context flagship for long cases",
    },
    "claude-opus-4-7": {
        "id": "vertex:anthropic/claude-opus-4-7",
        "label": "Claude Opus 4.7",
        "tier": "pro",
        "description": "Vertex AI · Anthropic flagship, strongest clinical reasoning",
    },
    "llama-3-3-70b": {
        "id": "vertex:meta/llama-3.3-70b-instruct-maas",
        "label": "Llama 3.3 70B",
        "tier": "pro",
        "description": "Vertex AI · Meta open-weight via managed endpoint",
    },
    "gpt-oss-20b": {
        "id": "openai/gpt-oss-20b:free",
        "label": "GPT-OSS 20B",
        "tier": "free",
        "description": "OpenAI open-weight via OpenRouter (free variant) · local-dev default",
    },
    "gpt-5": {
        "id": "openai/gpt-5",
        "label": "GPT-5",
        "tier": "pro",
        "description": "OpenAI flagship · routed through OpenRouter (only out-of-house model)",
    },
}

def _compute_default_model_key() -> str:
    """Pick the free-tier default based on whether Vertex AI is configured.

    Without Vertex env vars set, the gemini-flash-lite default would return
    `provider_unavailable` on every call — Vertex needs `VERTEX_PROJECT` + ADC.
    Fall back to OpenRouter's `gpt-oss-20b` so a dev with only OPENROUTER_API_KEY
    can run the app end-to-end. Detection mirrors the lifespan check in main.py.
    """
    if (
        os.environ.get("VERTEX_PROJECT")
        or os.environ.get("GCP_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
    ):
        return "gemini-2-5-flash-lite-free"
    return "gpt-oss-20b"


DEFAULT_MODEL_KEY = _compute_default_model_key()

# Back-compat alias so existing council.py agent definitions keep compiling
# until we migrate them to accept a per-run model override.
MODEL = MODELS[DEFAULT_MODEL_KEY]["id"]


def resolve_model(key: str | None, user_plan: Literal["free", "pro"]) -> tuple[str, bool]:
    """Resolve a model allowlist key to an OpenRouter slug, enforcing tier.

    Returns (slug, downgraded) where `downgraded=True` means the requested key
    was Pro-only but the user is on Free, so we silently fell back to the
    default free model. Callers should add `X-Model-Downgraded: 1` to the
    response in that case.
    """
    chosen_key = key if key in MODELS else DEFAULT_MODEL_KEY
    entry = MODELS[chosen_key]
    if entry["tier"] == "pro" and user_plan != "pro":
        return MODELS[DEFAULT_MODEL_KEY]["id"], True
    return entry["id"], False


def models_for_plan(user_plan: Literal["free", "pro"]) -> list[dict]:
    """Return the allowlist as a JSON-safe list, with a `locked` flag for UI lock icons."""
    return [
        {
            "key": key,
            "id": entry["id"],
            "label": entry["label"],
            "tier": entry["tier"],
            "description": entry["description"],
            "locked": entry["tier"] == "pro" and user_plan != "pro",
        }
        for key, entry in MODELS.items()
    ]

SPECIALIST_META: dict[str, dict] = {
    "internal_medicine": {
        "name": "Dr. Elena Vasquez",
        "specialty": "Internal Medicine & Primary Care",
        "initials": "EV",
        "color": "teal",
        "description": "General systemic assessment, first-line evaluation",
    },
    "cardiology": {
        "name": "Dr. James Okafor",
        "specialty": "Cardiology",
        "initials": "JO",
        "color": "red",
        "description": "Heart disease, arrhythmias, chest pain, hypertension",
    },
    "neurology": {
        "name": "Dr. Priya Sharma",
        "specialty": "Neurology",
        "initials": "PS",
        "color": "purple",
        "description": "Headaches, seizures, neuropathy, movement disorders",
    },
    "psychiatry": {
        "name": "Dr. Isabella Romano",
        "specialty": "Psychiatry",
        "initials": "IR",
        "color": "pink",
        "description": "Mood disorders, anxiety, psychosis, cognitive symptoms",
    },
    "pulmonology": {
        "name": "Dr. Yusuf Adeyemi",
        "specialty": "Pulmonology",
        "initials": "YA",
        "color": "blue",
        "description": "Respiratory disease, asthma, COPD, sleep apnea",
    },
    "gastroenterology": {
        "name": "Dr. Omar Farouq",
        "specialty": "Gastroenterology",
        "initials": "OF",
        "color": "amber",
        "description": "GI tract, liver, pancreas, IBD, GERD",
    },
    "endocrinology": {
        "name": "Dr. Fatima Al-Rashid",
        "specialty": "Endocrinology",
        "initials": "FA",
        "color": "green",
        "description": "Diabetes, thyroid, adrenal, hormonal disorders",
    },
    "rheumatology": {
        "name": "Dr. Aisha Patel",
        "specialty": "Rheumatology",
        "initials": "AP",
        "color": "coral",
        "description": "Autoimmune disease, arthritis, lupus, vasculitis",
    },
    "dermatology": {
        "name": "Dr. Lena Müller",
        "specialty": "Dermatology",
        "initials": "LM",
        "color": "pink",
        "description": "Skin conditions, rashes, lesions, hair and nail disorders",
    },
    "orthopedics": {
        "name": "Dr. Marcus Webb",
        "specialty": "Orthopedic Surgery",
        "initials": "MW",
        "color": "gray",
        "description": "Bone and joint injuries, spine, fractures, sports medicine",
    },
    "pharmacology": {
        "name": "Dr. Kenji Nakamura",
        "specialty": "Clinical Pharmacology",
        "initials": "KN",
        "color": "purple",
        "description": "Drug interactions, dosing, adverse effects, polypharmacy",
    },
    "gynecology": {
        "name": "Dr. Amina Hassan",
        "specialty": "Obstetrics & Gynecology",
        "initials": "AH",
        "color": "pink",
        "description": "Women's reproductive health, menstrual disorders, pregnancy",
    },
    "dentistry": {
        "name": "Dr. Marco Rossi",
        "specialty": "Oral Medicine & Dentistry",
        "initials": "MR",
        "color": "blue",
        "description": "Oral pain, jaw disorders, TMJ, dental infections",
    },
    "ophthalmology": {
        "name": "Dr. Nadia Petrov",
        "specialty": "Ophthalmology",
        "initials": "NP",
        "color": "teal",
        "description": "Eye pain, vision changes, retinal disease, glaucoma",
    },
    "ent": {
        "name": "Dr. David Kim",
        "specialty": "ENT — Ear, Nose & Throat",
        "initials": "DK",
        "color": "amber",
        "description": "Hearing loss, sinusitis, vertigo, voice disorders, throat",
    },
    "urology": {
        "name": "Dr. Carlos Mendez",
        "specialty": "Urology",
        "initials": "CM",
        "color": "blue",
        "description": "Urinary symptoms, kidney stones, prostate, incontinence",
    },
}

ALL_SPECIALIST_IDS = list(SPECIALIST_META.keys())


def specialist_list_for_prompts() -> str:
    """One line per specialist for embedding in agent instructions."""
    return "\n".join(
        f'  "{sid}": {meta["specialty"]} — {meta["description"]}'
        for sid, meta in SPECIALIST_META.items()
    )
