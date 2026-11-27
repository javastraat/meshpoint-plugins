"""RNode starting profiles per region (TODO 41, after upstream KMX415/meshpoint
``apps/reticulum/backend/regional.py``).

Starting values only: Settings -> Reticulum -> RNode radio's **Apply** fills
the form, the operator reviews and saves. Nothing is applied automatically
and saved settings are never moved. Ours adds "NL / our network" (exactly the
plugin's own defaults) as the first profile and the one an EU_868 Meshpoint
gets preselected; "EU" is upstream's EU_868 profile. These are not official
Reticulum channels, regulatory approval or a permission to transmit; peers
must use the same settings. Upstream's default airtime limits (10 % / 15 s,
1 % / hour) are not part of the profiles: the 869.4-869.65 MHz band allows
10 %, so the airtime caps are separate optional settings.
"""

from __future__ import annotations

import copy

_PROFILES = (
    # id, label, frequency Hz, bandwidth Hz, SF, CR, TX power dBm
    ("nl", "NL / our network", 869_463_000, 125_000, 8, 5, 20),
    ("eu", "EU (upstream)", 869_525_000, 125_000, 7, 5, 7),
    ("us", "US", 915_000_000, 500_000, 7, 5, 7),
    ("anz", "ANZ", 916_000_000, 500_000, 7, 5, 7),
    ("in", "IN", 866_000_000, 125_000, 7, 5, 7),
    ("kr", "KR", 921_900_000, 125_000, 7, 5, 7),
    ("sg", "SG", 923_000_000, 125_000, 7, 5, 7),
)

# Meshpoint radio.region -> preselected profile; anything else -> ours.
_BY_REGION = {"EU_868": "nl", "US": "us", "ANZ": "anz", "IN": "in", "KR": "kr", "SG_923": "sg"}
_FALLBACK = "nl"

NOTE = (
    "Starting values only: your peers must use the same settings, and local band, "
    "power and duty-cycle rules still apply. Apply fills the form; Save stores it."
)


def profiles() -> list[dict]:
    """Every profile (a fresh copy), ours first."""
    return copy.deepcopy([
        {
            "id": pid,
            "label": label,
            "settings": {
                "rnode_frequency_hz": freq,
                "rnode_bandwidth_hz": bw,
                "rnode_spreading_factor": sf,
                "rnode_coding_rate": cr,
                "rnode_tx_power": power,
            },
        }
        for pid, label, freq, bw, sf, cr, power in _PROFILES
    ])


def profile_for_region(region) -> str:
    """The profile id to preselect for a Meshpoint ``radio.region``."""
    return _BY_REGION.get(str(region or "").strip().upper(), _FALLBACK)


def describe(region) -> dict:
    """GET /api/reticulum/radio-profiles."""
    region = str(region or "").strip().upper() or "UNKNOWN"
    return {"region": region, "selected": profile_for_region(region),
            "profiles": profiles(), "note": NOTE}
