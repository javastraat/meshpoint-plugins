"""Plugin config, seeded from ``plugins.reticulum-repeaters.*`` at
``register()`` time and written back by the Settings tab.

    plugins:
      reticulum-repeaters:
        enabled: true
        polling_enabled: true
        interval_min: 15          # stats: all / radio / health / env
        lists_interval_min: 60    # neighbours / routes / links
        repeaters:
          - {hash: c3a9...e0, name: "NL-AMS-PD2EMC P1"}

Only repeaters in ``repeaters`` are ever contacted -- nothing is scanned
or auto-detected. Changes apply on the poller's next tick (no restart).
"""

from __future__ import annotations

import re
from typing import Any

SECTION = "reticulum-repeaters"
_HASH_RE = re.compile(r"^[0-9a-f]{32}$")

_DEFAULTS: dict[str, Any] = {
    "polling_enabled": True,
    "interval_min": 15,
    "lists_interval_min": 60,
    "repeaters": [],
}

_config: dict[str, Any] = dict(_DEFAULTS)


def _clean_repeaters(raw) -> list[dict]:
    out, seen = [], set()
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        h = str(r.get("hash") or "").strip().lower()
        if not _HASH_RE.match(h) or h in seen:
            continue
        seen.add(h)
        out.append({"hash": h, "name": str(r.get("name") or "")[:64]})
    return out


def init(config: dict) -> None:
    global _config
    merged = dict(_DEFAULTS)
    for key in _DEFAULTS:
        if config.get(key) not in (None, ""):
            merged[key] = config[key]
    merged["repeaters"] = _clean_repeaters(merged["repeaters"])
    merged["interval_min"] = _clamp(merged["interval_min"], 5, 1440, 15)
    merged["lists_interval_min"] = _clamp(merged["lists_interval_min"], 15, 1440, 60)
    merged["polling_enabled"] = bool(merged["polling_enabled"])
    _config = merged


def _clamp(v, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def valid_hash(h: str) -> bool:
    return bool(_HASH_RE.match(h or ""))


def to_dict() -> dict[str, Any]:
    return {**_config, "repeaters": [dict(r) for r in _config["repeaters"]]}


def repeaters() -> list[dict]:
    return [dict(r) for r in _config["repeaters"]]


def polling_enabled() -> bool:
    return bool(_config["polling_enabled"])


def interval_s() -> int:
    return int(_config["interval_min"]) * 60


def lists_interval_s() -> int:
    return int(_config["lists_interval_min"]) * 60


def set_settings(polling_enabled: bool, interval_min: int, lists_interval_min: int) -> None:
    _config["polling_enabled"] = bool(polling_enabled)
    _config["interval_min"] = _clamp(interval_min, 5, 1440, 15)
    _config["lists_interval_min"] = _clamp(lists_interval_min, 15, 1440, 60)
    _persist()


def add_repeater(h: str, name: str = "") -> bool:
    """False if invalid or already there."""
    h = (h or "").strip().lower()
    if not valid_hash(h) or any(r["hash"] == h for r in _config["repeaters"]):
        return False
    _config["repeaters"] = _config["repeaters"] + [{"hash": h, "name": (name or "")[:64]}]
    _persist()
    return True


def set_name(h: str, name: str) -> None:
    changed = False
    for r in _config["repeaters"]:
        if r["hash"] == h and name and r["name"] != name[:64]:
            r["name"] = name[:64]
            changed = True
    if changed:
        _persist()


def remove_repeater(h: str) -> bool:
    before = len(_config["repeaters"])
    _config["repeaters"] = [r for r in _config["repeaters"] if r["hash"] != h]
    if len(_config["repeaters"]) == before:
        return False
    _persist()
    return True


def _persist() -> None:
    """Write back to plugins.reticulum-repeaters, keeping whatever else is
    in that section (the Settings -> Plugins "enabled" toggle)."""
    try:
        import yaml

        from src.config import _get_local_yaml_path, save_section_to_yaml  # noqa: SLF001
    except ImportError:  # tests / dev Mac without meshpoint core
        return
    path = _get_local_yaml_path()
    current: dict = {}
    if path.exists():
        with open(path) as fh:
            data = yaml.safe_load(fh) or {}
        section = (data.get("plugins") or {}).get(SECTION)
        if isinstance(section, dict):
            current = dict(section)
    current.update(to_dict())
    save_section_to_yaml("plugins", {SECTION: current})
