"""ATEM model profile registry.

A profile describes the user-facing model we want to emulate.
Only profiles with implemented=True are selectable by the emulator.
"""

PROFILES = {
    "tvstudio-hd": {
        "name": "ATEM Television Studio HD",
        "family": "television-studio",
        "implemented": True,
        "bootstrap": "pyatemsim-tvstudio-hd",
        "notes": "Reference profile used to validate the protocol engine.",
    },
    "mini": {
        "name": "ATEM Mini",
        "family": "mini",
        "implemented": True,
        "bootstrap": "profiles_data/mini-v8.6.data",
        "notes": "Captured startup state from a real ATEM Mini.",
    },
    "mini-pro": {
        "name": "ATEM Mini Pro",
        "family": "mini",
        "implemented": True,
        "bootstrap": "profiles_data/mini-pro-v8.2.data",
        "notes": "Captured startup state from a real ATEM Mini Pro.",
    },
    "mini-pro-iso": {
        "name": "ATEM Mini Pro ISO",
        "family": "mini",
        "implemented": True,
        "bootstrap": "profiles_data/mini-pro-iso-v8.4.data",
        "notes": "Captured startup state from a real ATEM Mini Pro ISO.",
    },
    "mini-extreme": {
        "name": "ATEM Mini Extreme",
        "family": "mini",
        "implemented": True,
        "bootstrap": "profiles_data/mini-extreme-v8.6.data",
        "notes": "Captured startup state from a real ATEM Mini Extreme (protocol v8.6).",
    },
    "mini-extreme-iso": {
        "name": "ATEM Mini Extreme ISO",
        "family": "mini",
        "implemented": True,
        "bootstrap": "profiles_data/mini-extreme-iso-v9.5.data",
        "notes": "Captured startup state from a real ATEM Mini Extreme ISO.",
    },
    "constellation-1me-hd": {
        "name": "ATEM 1 M/E Constellation HD",
        "family": "constellation",
        "implemented": False,
    },
    "constellation-2me-hd": {
        "name": "ATEM 2 M/E Constellation HD",
        "family": "constellation",
        "implemented": True,
        "bootstrap": "profiles_data/constellation-2me-hd-v9.6.2.data",
        "notes": "Captured startup state from a real ATEM 2 M/E Constellation HD.",
    },
    "constellation-4me-hd": {
        "name": "ATEM 4 M/E Constellation HD",
        "family": "constellation",
        "implemented": False,
    },
    "constellation-4me-4k": {
        "name": "ATEM 4 M/E Constellation 4K",
        "family": "constellation",
        "implemented": True,
        "bootstrap": "profiles_data/constellation-4me-4k-v9.1.data",
        "notes": "Captured startup state from a real ATEM 4 M/E Constellation 4K.",
    },
    "tvs-hd8": {
        "name": "ATEM Television Studio HD8",
        "family": "television-studio",
        "implemented": True,
        "bootstrap": "profiles_data/tvs-hd8-v9.0.data",
        "notes": "Captured startup state from a real ATEM Television Studio HD8.",
    },
}


def get_profile(slug):
    return PROFILES.get(slug)


def implemented_profiles():
    return {k: v for k, v in PROFILES.items() if v.get("implemented")}


def format_profiles():
    lines = []
    for slug, profile in PROFILES.items():
        state = "READY" if profile.get("implemented") else "PLANNED"
        lines.append(f"{slug:24} {state:7} {profile['name']}")
    return "\n".join(lines)
