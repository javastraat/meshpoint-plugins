"""Plugin settings, from ``plugins.localmesh`` in the Meshpoint config:

    plugins:
      localmesh:
        enabled: true
        devices:
          - serial_port: /dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_F8:5B:1B:A7:1A:8C-if00
            label: base

Seeded once from ``reg.config`` at register() time; the Settings tab changes the device list through set_devices(),
which writes it back to local.yaml. A receiver reads its port once when it is built, so a change takes effect on the
next service restart, the same as every other plugin's config change.
"""

from __future__ import annotations

_devices: list[dict] = []


def init(config: dict) -> None:
    global _devices
    _devices = [dict(d) for d in (config.get("devices") or [])]


def devices() -> list[dict]:
    return [dict(d) for d in _devices]


def to_dict() -> dict:
    return {"devices": devices()}


def set_devices(devices: list[dict]) -> None:
    """Replace the device list and persist."""
    global _devices
    _devices = [dict(d) for d in devices]
    _persist()


def _current_saved_config() -> dict:
    """plugins.localmesh as it is on disk now, not as it was at init(): save_section_to_yaml merges one level deep, so
    writing only our own keys would otherwise undo a later Settings -> Plugins enable/disable (same as dapnet)."""
    import yaml

    from src.config import _get_local_yaml_path  # noqa: SLF001 -- same as the dapnet plugin

    path = _get_local_yaml_path()
    if not path.exists():
        return {}
    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    section = data.get("plugins")
    if not isinstance(section, dict):
        return {}
    current = section.get("localmesh")
    return dict(current) if isinstance(current, dict) else {}


def _persist() -> None:
    from src.config import save_section_to_yaml

    current = _current_saved_config()
    current.update(to_dict())
    save_section_to_yaml("plugins", {"localmesh": current})
