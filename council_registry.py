"""
MedAI Council — shared registry (specialists, model id, prompt fragments).
"""

from __future__ import annotations

# Newest NVIDIA flagship on OpenRouter (Dec 2025).
MODEL = "nvidia/nemotron-3-super-120b-a12b"

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
