"""Plugin settings, from ``plugins.localmesh`` in the Meshpoint config:

    plugins:
      localmesh:
        enabled: true
        devices:
          - serial_port: /dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_F8:5B:1B:A7:1A:8C-if00
            label: base
        channel_names:
          "0": Algemeen
          "1": Noodkanaal

Seeded once from ``reg.config`` at register() time; the Settings tab changes the device list through set_devices(),
which writes it back to local.yaml. A receiver reads its port once when it is built, so a change takes effect on the
next service restart, the same as every other plugin's config change.

``channel_names`` is a Meshpoint-side override, independent of (and preferred over) whatever name a receiver itself
reports for a channel slot (CHANNELS_GET/CHANNEL_EVENT, kept per-receiver in LocalMeshSerialSource.channels) -- a
receiver often never reports a name at all, leaving every channel to fall back to the generic "channel N". No
restart needed: it is just a lookup table read at render/save time, unlike the device list. Stored string-keyed
(YAML/JSON have no integer map keys) but handled as ``dict[int, str]`` everywhere else in this plugin.
"""

from __future__ import annotations

_devices: list[dict] = []
_channel_names: dict[int, str] = {}


def init(config: dict) -> None:
    global _devices, _channel_names
    _devices = [dict(d) for d in (config.get("devices") or [])]
    _channel_names = _parse_channel_names(config.get("channel_names") or {})


def _parse_channel_names(raw: dict) -> dict[int, str]:
    out: dict[int, str] = {}
    for k, v in raw.items():
        try:
            ch = int(k)
        except (TypeError, ValueError):
            continue   # a hand-edited local.yaml with a bad key -- skip it rather than crash the plugin
        name = str(v).strip()
        if name:
            out[ch] = name
    return out


def devices() -> list[dict]:
    return [dict(d) for d in _devices]


def channel_names() -> dict[int, str]:
    return dict(_channel_names)


def channel_name(channel: int) -> str:
    """The configured override name for this channel, or "" if none is set -- callers already have their own
    fallback chain (the receiver's own reported name, then "channel N"), same shape as everywhere else in this
    plugin that resolves a display name."""
    return _channel_names.get(channel, "")


def to_dict() -> dict:
    return {"devices": devices(), "channel_names": {str(k): v for k, v in _channel_names.items()}}


def set_devices(devices: list[dict]) -> None:
    """Replace the device list and persist."""
    global _devices
    _devices = [dict(d) for d in devices]
    _persist()


def set_channel_names(names: dict[int, str]) -> None:
    """Replace the channel-name overrides and persist. Caller does its own validation (range, non-empty names) --
    this just stores whatever dict[int, str] it's handed, same split of responsibility as set_devices()."""
    global _channel_names
    _channel_names = dict(names)
    _persist()


def set_name(label: str, name: str) -> None:
    """Remember the name the Send tab gave a receiver (the board itself does not report it), and persist."""
    for d in _devices:
        if d.get("label", "") == label:
            d["name"] = name
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
