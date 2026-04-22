"""
MedAI Council — shared registry (specialists, model allowlist, prompt fragments).
"""

from __future__ import annotations

from typing import Literal, TypedDict


class ModelEntry(TypedDict):
    id: str
    label: str
    tier: Literal["free", "pro"]
    description: str


# Curated allowlist. Keys are stable identifiers the frontend sends; `id` is the
# OpenRouter slug routed through MultiProvider in main.py's startup.
MODELS: dict[str, ModelEntry] = {
    "nvidia-nemotron-free": {
        "id": "nvidia/nemotron-3-super-120b-a12b:free",
        "label": "Nemotron 120B",
        "tier": "free",
        "description": "NVIDIA flagship open-weight · free tier",
    },
    "claude-opus-4-7": {
        "id": "anthropic/claude-opus-4.7",
        "label": "Claude Opus 4.7",
        "tier": "pro",
        "description": "Anthropic's most capable model",
    },
    "gpt-5": {
        "id": "openai/gpt-5",
        "label": "GPT-5",
        "tier": "pro",
        "description": "OpenAI's flagship reasoning model",
    },
    "gemini-2-5-pro": {
        "id": "google/gemini-2.5-pro",
        "label": "Gemini 2.5 Pro",
        "tier": "pro",
        "description": "Google's flagship with 1M context",
    },
    "deepseek-r1": {
        "id": "deepseek/deepseek-r1",
        "label": "DeepSeek R1",
        "tier": "pro",
        "description": "Strong reasoning at lower cost",
    },
}

DEFAULT_MODEL_KEY = "nvidia-nemotron-free"

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
