"""Interfaces rnsd discovered on the network -- the repeater layer on the
Reticulum Dashboard map.

With ``discover_interfaces = Yes`` in its ``[reticulum]`` section (the
``discover_interfaces`` setting, see ``write_rnsd_config.py``), rnsd
listens for interface-discovery announces (aspect
``rnstransport.discovery.interface`` -- what rmap.world collects, and what
ScotMesh repeaters, discoverable meshpoints and public TCP/backbone nodes
send), checks their stamps and keeps one msgpack file per interface in
``<configdir>/storage/discovery/interfaces/`` (RNS ``Discovery.py``'s
``InterfaceDiscovery.interface_discovered``). This module only READS those
files: rnsd itself removes entries older than 7 days, and
``list_discovered_interfaces()`` in RNS deletes files as a side effect, so
it's deliberately not used here.

Only whitelisted fields leave this module -- a stored entry can also carry
``ifac_netkey`` (an interface passphrase) and a ready-made ``config_entry``
containing it, which must never reach the dashboard. ``reachable_on`` /
``port`` (the public host of a TCP/backbone node) are returned only when
the caller asks for them (admins).

FastAPI-free; RNS is imported lazily so the tests can inject a decoder.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Same thresholds as RNS Discovery.InterfaceDiscovery (THRESHOLD_UNKNOWN /
# THRESHOLD_STALE / THRESHOLD_REMOVE).
UNKNOWN_AFTER_S = 24 * 60 * 60
STALE_AFTER_S = 3 * 24 * 60 * 60
GONE_AFTER_S = 7 * 24 * 60 * 60

_CACHE_TTL_S = 30.0
_cache: dict = {"key": None, "at": 0.0, "items": []}


def storage_dir(config_dir: str) -> str:
    """Where rnsd keeps discovered interfaces for this configdir."""
    return os.path.join(config_dir, "storage", "discovery", "interfaces")


def _status(age_s: float) -> str:
    if age_s > STALE_AFTER_S:
        return "stale"
    if age_s > UNKNOWN_AFTER_S:
        return "unknown"
    return "available"


def _num(v) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _text(v) -> Optional[str]:
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    return v if isinstance(v, str) and v else None


def _entry(info: dict, now: float) -> Optional[dict]:
    """One stored file -> the dashboard's dict, or ``None`` when it has no
    usable location or is past RNS's own 7-day removal age."""
    lat, lon = _num(info.get("latitude")), _num(info.get("longitude"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    if lat == 0 and lon == 0:
        return None
    last = _num(info.get("last_heard")) or _num(info.get("received"))
    if last is None:
        return None
    age = max(0.0, now - last)
    if age > GONE_AFTER_S:
        return None
    out = {
        "name": _text(info.get("name")) or "Unnamed",
        "type": _text(info.get("type")) or "?",
        "software": _text(info.get("impl_name")),
        "version": _text(info.get("version")),
        "transport": bool(info.get("transport")),
        "transport_id": _text(info.get("transport_id")),
        "network_id": _text(info.get("network_id")),
        "hops": int(info["hops"]) if isinstance(info.get("hops"), int) else None,
        "latitude": lat,
        "longitude": lon,
        "height": _num(info.get("height")),
        "first_heard": _num(info.get("discovered")),
        "last_heard": last,
        "heard_count": int(info["heard_count"]) if isinstance(info.get("heard_count"), int) else None,
        "status": _status(age),
    }
    if out["type"] == "RNodeInterface":
        for key in ("frequency", "bandwidth", "sf", "cr"):
            out[key] = int(info[key]) if isinstance(info.get(key), int) else None
    host = _text(info.get("reachable_on"))
    if host:
        out["reachable_on"] = host
        out["port"] = int(info["port"]) if isinstance(info.get("port"), int) else None
    return out


def _default_unpackb() -> Callable[[bytes], object]:
    import RNS.vendor.umsgpack as umsgpack  # noqa: PLC0415 -- lazy, see module docstring

    return umsgpack.unpackb


def read_discovered(
    config_dir: str,
    now: Optional[float] = None,
    unpackb: Optional[Callable[[bytes], object]] = None,
) -> list[dict]:
    """Every discovered interface with a location, most recently heard
    first. A missing directory (setting just turned on, nothing heard yet)
    is an empty list; an unreadable file is logged and skipped."""
    path = storage_dir(config_dir)
    try:
        names = os.listdir(path)
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.info("Reticulum: cannot list %s: %s", path, exc)
        return []
    now = time.time() if now is None else now
    unpack = unpackb or _default_unpackb()
    items: list[dict] = []
    for name in names:
        try:
            with open(os.path.join(path, name), "rb") as fh:
                info = unpack(fh.read())
            entry = _entry(info, now) if isinstance(info, dict) else None
        except Exception as exc:  # noqa: BLE001 -- one bad file never hides the rest
            logger.info("Reticulum: skipped discovered interface %s: %s", name, exc)
            continue
        if entry:
            items.append(entry)
    items.sort(key=lambda e: e["last_heard"], reverse=True)
    return items


def list_for(config_dir: str, include_address: bool) -> list[dict]:
    """``read_discovered`` behind a short cache (the dashboard polls), with
    the TCP/backbone address stripped unless ``include_address``."""
    now = time.monotonic()
    if _cache["key"] != config_dir or now - _cache["at"] > _CACHE_TTL_S:
        _cache.update(key=config_dir, at=now, items=read_discovered(config_dir))
    items = _cache["items"]
    if include_address:
        return [dict(e) for e in items]
    return [{k: v for k, v in e.items() if k not in ("reachable_on", "port")} for e in items]


def reset_cache() -> None:
    _cache.update(key=None, at=0.0, items=[])
