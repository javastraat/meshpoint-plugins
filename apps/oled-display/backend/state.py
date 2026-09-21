"""In-memory oled-display plugin settings, seeded once from
``plugins.oled-display.*`` at ``register()`` time. Same pattern as the
offline-map / Reticulum plugins' own ``backend/state.py``.

    plugins:
      oled-display:
        enabled: true
        i2c_address: "0x3D"
        driver: ssd1306       # ssd1306 | sh1106 | ssd1309
        width: 128
        height: 64
        blank_after_minutes: 30   # 0 = never blank
        refresh_seconds: 5
        boot_logo_seconds: 3      # how long the MESHPOINT boot logo shows, 0 = skip it

Settings changes apply on the next service restart (the display service
reads these once at ``build()`` time, same as Reticulum's RNode/backbone
config) -- there's no live "Start"/"Stop" button here the way
offline-map's subprocess has, since this is a lifespan-managed service.
"""

from __future__ import annotations

from typing import Any

_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "i2c_address": "0x3D",
    "driver": "ssd1306",
    "width": 128,
    "height": 64,
    "blank_after_minutes": 30,
    "refresh_seconds": 5,
    "boot_logo_seconds": 3,
}

_config: dict[str, Any] = dict(_DEFAULTS)


def init(config: dict) -> None:
    """Seed from ``reg.config`` (a copy of ``plugins.oled-display``). A
    missing or blank key falls back to the default -- a user-edited YAML
    typo must not crash the plugin."""
    global _config
    merged = dict(_DEFAULTS)
    for key in _DEFAULTS:
        value = config.get(key)
        if value is not None and value != "":
            merged[key] = value
    _config = merged


def to_dict() -> dict[str, Any]:
    return dict(_config)


_ALLOWED_UPDATE_KEYS = frozenset(_DEFAULTS)


def set_config(updates: dict) -> None:
    """Merge settings-page values (already validated by routes.py's
    pydantic model) into state and persist to ``plugins.oled-display``.
    Takes effect on the next service restart."""
    global _config
    merged = dict(_config)
    for key, value in updates.items():
        if key in _ALLOWED_UPDATE_KEYS:
            merged[key] = value
    _config = merged
    _persist()


def _current_saved_config() -> dict:
    """Read ``plugins.oled-display``'s CURRENT on-disk shape (not this
    module's load-time snapshot) so a settings save never clobbers a
    same-session Settings -> Plugins enable/disable toggle -- same
    reasoning as the offline-map / Reticulum / DAPNET plugins' own
    state._current_saved_config()."""
    import yaml

    from src.config import _get_local_yaml_path  # noqa: SLF001 -- see docstring

    path = _get_local_yaml_path()
    if not path.exists():
        return {}
    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    section = data.get("plugins")
    if not isinstance(section, dict):
        return {}
    current = section.get("oled-display")
    return dict(current) if isinstance(current, dict) else {}


def _persist() -> None:
    from src.config import save_section_to_yaml

    current = _current_saved_config()
    current.update(to_dict())
    save_section_to_yaml("plugins", {"oled-display": current})
