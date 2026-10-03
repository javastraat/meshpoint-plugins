"""Offline lookup data for the Bluetooth scanner: who made a device, and
what it advertises.

Three sources, all free, downloaded once (by ``setup.sh``, or the page's
"Refresh" button) into ``<meshpoint>/data/bluetooth-scanner/`` and
converted to small JSON files -- nothing is looked up online at runtime:

- **MAC vendors** (maclookup.app's CSV, IEEE OUI data): only meaningful
  for *public* addresses. Many BLE devices (phones, watches, earbuds,
  trackers) advertise from a random, rotating address with no vendor
  prefix at all -- see ``address_kind()`` -- so those get no MAC vendor.
- **Bluetooth SIG company identifiers**: the 16-bit company ID at the
  start of an advertisement's manufacturer data (Apple = 0x004C, ...).
  This works for random addresses too, so it's what names most devices.
- **Bluetooth SIG 16-bit service UUIDs** (standard + member UUIDs), plus a
  few well-known 128-bit ones (Meshtastic, Nordic UART), and the SIG's
  appearance categories (Phone, Watch, Audio Sink, ...).

Every lookup degrades gracefully: without the downloaded files a small
built-in table still names the most common companies and services.

Stdlib only (plus PyYAML, already a Meshpoint dependency, imported only
while converting) -- also runnable as a script for ``setup.sh``:
``python3 vendor_db.py --update``.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# <meshpoint>/plugins/apps/bluetooth-scanner/backend/vendor_db.py -> <meshpoint>
_REPO = Path(__file__).resolve().parents[4]
DATA_DIR = _REPO / "data" / "bluetooth-scanner"

MAC_CSV_URL = "https://maclookup.app/downloads/csv-database/get-db"
_SIG = "https://bitbucket.org/bluetooth-SIG/public/raw/main/assigned_numbers"
COMPANY_URL = f"{_SIG}/company_identifiers/company_identifiers.yaml"
SERVICE_URLS = (f"{_SIG}/uuids/service_uuids.yaml", f"{_SIG}/uuids/member_uuids.yaml")
APPEARANCE_URL = f"{_SIG}/core/appearance_values.yaml"

_BT_BASE_SUFFIX = "-0000-1000-8000-00805f9b34fb"

# Fallback when the downloaded files are absent. Values checked against
# the SIG's company_identifiers.yaml (2026-10).
_BUILTIN_COMPANIES = {
    0x004C: "Apple, Inc.", 0x0006: "Microsoft", 0x00E0: "Google",
    0x0075: "Samsung Electronics Co. Ltd.", 0x0087: "Garmin International, Inc.",
    0x0059: "Nordic Semiconductor ASA", 0x02E5: "Espressif Systems (Shanghai) Co., Ltd.",
    0x012D: "Sony Corporation", 0x009E: "Bose Corporation",
    0x027D: "HUAWEI Technologies Co., Ltd.", 0x038F: "Xiaomi Inc.",
    0x067C: "Tile, Inc.", 0x0171: "Amazon.com Services LLC",
    0x058E: "Meta Platforms Technologies, LLC", 0x01DA: "Logitech International SA",
    0x0499: "Ruuvi Innovations Ltd.",
}
_BUILTIN_SERVICES = {
    "1800": "GAP", "1801": "GATT", "180a": "Device Information", "180d": "Heart Rate",
    "180f": "Battery", "1812": "Human Interface Device", "181a": "Environmental Sensing",
    "fd6f": "Exposure Notification", "feaa": "Google LLC (Eddystone)",
    "fe2c": "Google LLC (Fast Pair)", "feed": "Tile, Inc.", "feec": "Tile, Inc.",
}
# 128-bit UUIDs worth naming that the SIG lists don't cover.
_KNOWN_128 = {
    "6ba1b218-15a8-461f-9fa8-5dcae273eafd": "Meshtastic",
    "6e400001-b5a3-f393-e0a9-e50e24dcca9e": "Nordic UART (RNode, MeshCore, ...)",
}
# Apple "Continuity" message types: first byte of Apple's manufacturer data.
# (Published Continuity protocol research; only the well-established ones.)
_APPLE_TYPES = {
    0x02: "iBeacon", 0x05: "AirDrop", 0x07: "AirPods / Beats", 0x09: "AirPlay target",
    0x0A: "AirPlay source", 0x0C: "Handoff", 0x0D: "Tethering target", 0x0E: "Hotspot",
    0x0F: "Nearby Action", 0x10: "Nearby Info", 0x12: "Find My",
}
# Bluetooth appearance: category = value >> 6. Fallback subset, checked
# against the SIG's core/appearance_values.yaml (2026-10); the full list
# is downloaded with the rest.
_APPEARANCE = {
    0x01: "Phone", 0x02: "Computer", 0x03: "Watch", 0x04: "Clock", 0x05: "Display",
    0x06: "Remote Control", 0x07: "Eye-glasses", 0x08: "Tag", 0x09: "Keyring",
    0x0A: "Media Player", 0x0B: "Barcode Scanner", 0x0C: "Thermometer",
    0x0D: "Heart Rate Sensor", 0x0E: "Blood Pressure", 0x0F: "Human Interface Device",
    0x10: "Glucose Meter", 0x11: "Running Walking Sensor", 0x12: "Cycling",
    0x13: "Control Device", 0x14: "Network Device", 0x15: "Sensor",
    0x16: "Light Fixtures", 0x1C: "Access Control", 0x1E: "Power Device",
    0x1F: "Light Source", 0x21: "Audio Sink", 0x22: "Audio Source",
    0x25: "Wearable Audio Device", 0x29: "Hearing aid", 0x2A: "Gaming",
    0x31: "Pulse Oximeter", 0x32: "Weight Scale", 0x51: "Outdoor Sports Activity",
}

_cache: dict = {}


# ── address classification ─────────────────────────────────────────

def address_kind(address: str, address_type: Optional[str] = None) -> str:
    """'public', or the random sub-type: 'random static',
    'random (resolvable private)', 'random (non-resolvable)'.

    Uses BlueZ's AddressType when the scanner has it. Without it, a guess:
    the locally-administered bit (0x02 of the first octet) is never set on
    a vendor-assigned MAC, so set = random. Clear is ambiguous (a random
    static address can have it clear too), so then: a known vendor prefix
    = public; top bits 11 with no known prefix = random static; otherwise
    public. E.g. C0:28:8D:... is Logitech's real prefix, not a random one.
    """
    try:
        first = int(address.split(":")[0], 16)
    except (ValueError, IndexError):
        return "unknown"
    if address_type == "public":
        return "public"
    if address_type != "random" and not first & 0x02:
        if first >> 6 == 0b11 and _load("macs.json") and mac_vendor(address) is None:
            return "random static"
        return "public"
    top = first >> 6
    if top == 0b11:
        return "random static"
    if top == 0b01:
        return "random (resolvable private)"
    return "random (non-resolvable)"


# ── loading ────────────────────────────────────────────────────────

def _load(name: str) -> dict:
    if name not in _cache:
        path = DATA_DIR / name
        try:
            _cache[name] = json.loads(path.read_text()) if path.exists() else {}
        except (OSError, ValueError):
            logger.warning("bluetooth-scanner: unreadable %s, ignoring", path)
            _cache[name] = {}
    return _cache[name]


def reload() -> None:
    _cache.clear()


def status() -> dict:
    meta = _load("meta.json")
    return {
        "present": bool(meta),
        "updated_at": meta.get("updated_at"),
        "counts": meta.get("counts", {}),
    }


# ── lookups ────────────────────────────────────────────────────────

def mac_vendor(address: str) -> Optional[str]:
    """Longest-prefix match (36-, 28-, then 24-bit IEEE blocks)."""
    hexaddr = address.replace(":", "").replace("-", "").upper()
    macs = _load("macs.json")
    for n in (9, 7, 6):
        hit = macs.get(hexaddr[:n])
        if hit:
            return hit
    return None


def company_name(company_id: int) -> Optional[str]:
    return _load("companies.json").get(str(company_id)) or _BUILTIN_COMPANIES.get(company_id)


def service_name(uuid: str) -> Optional[str]:
    u = uuid.lower()
    if u in _KNOWN_128:
        return _KNOWN_128[u]
    short = u[4:8] if u.endswith(_BT_BASE_SUFFIX) and u.startswith("0000") else (u if len(u) == 4 else None)
    if short is None:
        return None
    return _load("services.json").get(short) or _BUILTIN_SERVICES.get(short)


def appearance_name(value: Optional[int]) -> Optional[str]:
    if value is None:
        return None
    category = int(value) >> 6
    return _load("appearance.json").get(str(category)) or _APPEARANCE.get(category)


def apple_type(payload_hex: str) -> Optional[str]:
    try:
        return _APPLE_TYPES.get(int(payload_hex[:2], 16))
    except ValueError:
        return None


# ── download + convert ─────────────────────────────────────────────

def _fetch(url: str, timeout: float = 60.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "meshpoint-bluetooth-scanner"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_mac_csv(text: str) -> dict[str, str]:
    """maclookup.app CSV -> {hex prefix (6/7/9 digits): vendor}."""
    out: dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(text)):
        prefix = (row.get("Mac Prefix") or "").replace(":", "").upper()
        vendor = (row.get("Vendor Name") or "").strip()
        if prefix and vendor:
            out[prefix] = vendor
    return out


def parse_companies(text: str) -> dict[str, str]:
    import yaml
    data = yaml.safe_load(text) or {}
    return {str(int(c["value"])): str(c["name"]) for c in data.get("company_identifiers", [])}


def parse_services(*texts: str) -> dict[str, str]:
    import yaml
    out: dict[str, str] = {}
    for text in texts:
        data = yaml.safe_load(text) or {}
        for u in data.get("uuids", []):
            out[f"{int(u['uuid']):04x}"] = str(u["name"])
    return out


def parse_appearance(text: str) -> dict[str, str]:
    import yaml
    data = yaml.safe_load(text) or {}
    rows = next((v for v in data.values() if isinstance(v, list)), [])
    return {str(int(c["category"])): str(c["name"]) for c in rows}


def update() -> dict:
    """Download all sources and replace the JSON files. Raises on a failed
    download (the old files are kept -- every file is written atomically)."""
    macs = parse_mac_csv(_fetch(MAC_CSV_URL).decode("utf-8", "replace"))
    companies = parse_companies(_fetch(COMPANY_URL).decode("utf-8"))
    services = parse_services(*(_fetch(u).decode("utf-8") for u in SERVICE_URLS))
    appearance = parse_appearance(_fetch(APPEARANCE_URL).decode("utf-8"))
    if not macs or not companies or not services:
        raise RuntimeError("a lookup source came back empty; keeping the old files")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    counts = {"mac_prefixes": len(macs), "companies": len(companies), "services": len(services)}
    for name, obj in (("macs.json", macs), ("companies.json", companies),
                      ("services.json", services), ("appearance.json", appearance),
                      ("meta.json", {"updated_at": time.time(), "counts": counts})):
        tmp = DATA_DIR / f".{name}.tmp"
        tmp.write_text(json.dumps(obj, separators=(",", ":")))
        tmp.replace(DATA_DIR / name)
    reload()
    logger.info("bluetooth-scanner: vendor database updated %s", counts)
    return status()


if __name__ == "__main__":  # pragma: no cover -- used by setup.sh
    if "--update" in sys.argv:
        try:
            print(update())
        except Exception as exc:  # noqa: BLE001
            print(f"vendor database download failed: {exc}", file=sys.stderr)
            sys.exit(1)
