"""In-memory offline-map plugin settings, seeded once from
``plugins.offline-map.*`` at ``register()`` time. Same pattern as the
Reticulum plugin's own ``backend/state.py``.

    plugins:
      offline-map:
        port: 8080
        maps_directory: data/offline-map/maps
        presets_directory: data/offline-map/presets
        log_file: data/offline-map/offline-map.log
        max_workers: 50
        rate_limit: 50
        max_retries: 5
        quiet: false

Defaults for maps/presets/log all live under this repo's own ``data/``
(same convention as ``data/reticulum/``, ``data/tls/``) rather than inside
this plugin's own folder -- a plugin reinstall/update never touches
downloaded tiles, and it's the one directory Meshpoint's own backup
tooling already knows to include.

Unlike Reticulum's RNode/backbone settings (only read once at rnsd
startup), these are read fresh by ``process.py`` every time "Start" is
pressed -- so a settings change here applies on the next start, no
Meshpoint service restart needed.
"""

from __future__ import annotations

from typing import Any

_DEFAULTS: dict[str, Any] = {
    "port": 8080,
    "maps_directory": "data/offline-map/maps",
    "presets_directory": "data/offline-map/presets",
    "log_file": "data/offline-map/offline-map.log",
    "max_workers": 50,
    "rate_limit": 50,
    "max_retries": 5,
    "quiet": False,
}

_config: dict[str, Any] = dict(_DEFAULTS)


def init(config: dict) -> None:
    """Seed from ``reg.config`` (a copy of ``plugins.offline-map``). A
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
    """Merge settings-page values (already validated by routes.py's pydantic
    model) into state and persist to ``plugins.offline-map``. Takes effect
    on the next "Start" -- process.py builds its argv from this module
    fresh each time, it doesn't cache anything at construction."""
    global _config
    merged = dict(_config)
    for key, value in updates.items():
        if key in _ALLOWED_UPDATE_KEYS:
            merged[key] = value
    _config = merged
    _persist()


def _current_saved_config() -> dict:
    """Read ``plugins.offline-map``'s CURRENT on-disk shape (not this
    module's load-time snapshot) so a settings save never clobbers a
    same-session Settings -> Plugins enable/disable toggle -- same
    reasoning as the Reticulum/DAPNET plugins' own
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
    current = section.get("offline-map")
    return dict(current) if isinstance(current, dict) else {}


def _persist() -> None:
    from src.config import save_section_to_yaml

    current = _current_saved_config()
    current.update(to_dict())
    save_section_to_yaml("plugins", {"offline-map": current})
