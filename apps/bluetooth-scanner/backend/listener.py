"""Bluetooth LE scanner listener.

Wraps bleak's async BLE scanning (talks to BlueZ over D-Bus on Linux)
into a live, MAC-keyed table of nearby advertising devices -- address,
name, RSSI, last-seen -- refreshed in place rather than appended to a
log, since the same device re-advertises constantly and a scrolling
log would just be repetition, not new information.

No shared-resource registry claim like the RTL-SDR family of
listeners: BLE scanning uses the Pi's own onboard (or USB) Bluetooth
adapter, which nothing else in Meshpoint touches, so there's no
contention to arbitrate.

Each row also carries what the advertisement says about the device
(address type, manufacturer data, service UUIDs/data, TX power,
appearance), merged across adverts -- a device often splits that over its
advert and its scan response -- and named via ``vendor_db`` (MAC vendor
for public addresses, Bluetooth SIG company/service names for all).

``bleak`` is imported lazily inside ``start()``, not at module level,
so this file -- and its tests -- import cleanly even when bleak isn't
installed yet (matches ``check.sh``'s "setup needed" story instead of
crashing the whole plugin load).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from . import vendor_db

logger = logging.getLogger(__name__)

# The page hides a device that hasn't re-advertised in this long while a
# scan runs (its "Hide devices not seen for 2 min" checkbox, on by
# default) -- the backend no longer drops them itself, so a stopped scan
# keeps its last table and the checkbox can show everything.
_STALE_AFTER_SECONDS = 120.0
# Hard cap so a long scan with that checkbox off can't grow forever:
# beyond this, the least recently seen devices are dropped.
_MAX_DEVICES = 2000

# Auto-stop if nobody's polled /status in this long -- a forgotten
# open tab shouldn't scan (and hold the adapter) forever.
_IDLE_STOP_SECONDS = 600.0
_IDLE_CHECK_INTERVAL_SECONDS = 30.0


class BluetoothScannerListener:
    def __init__(self) -> None:
        self._scanner = None
        self._devices: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        self._last_error: Optional[str] = None
        self._last_poll_at: Optional[float] = None
        self._idle_task: Optional[asyncio.Task] = None

    @property
    def running(self) -> bool:
        return self._scanner is not None

    async def start(self) -> None:
        async with self._lock:
            if self._scanner is not None:
                return
            try:
                from bleak import BleakScanner
            except ImportError as exc:
                raise RuntimeError(
                    "bleak is not installed -- run: "
                    "sudo meshpoint plugin setup bluetooth-scanner"
                ) from exc

            self._last_error = None
            scanner = BleakScanner(detection_callback=self._on_detection)
            try:
                await scanner.start()
            except Exception as exc:  # noqa: BLE001 -- surface whatever bleak/BlueZ raises
                detail = f"{type(exc).__name__}: {exc}"
                # A real, distinct failure mode confirmed live: bleak
                # reaches bluetoothd over D-Bus fine (unlike a missing-
                # adapter/D-Bus-timeout error) but BlueZ's own adapter
                # power state is off -- rfkill-unblocked and
                # hciconfig-up don't imply this. Matching on the
                # message text rather than a specific bleak exception
                # class, since that class has moved across bleak
                # versions and this substring hasn't.
                if "power" in str(exc).lower():
                    self._last_error = (
                        f"{detail} -- run: sudo bluetoothctl power on "
                        "(or re-run plugin setup, which now does this "
                        "automatically)"
                    )
                else:
                    self._last_error = detail
                raise RuntimeError(self._last_error) from exc

            self._scanner = scanner
            self._last_poll_at = time.time()
            self._idle_task = asyncio.create_task(
                self._idle_watchdog(), name="bluetooth-scanner-idle-watchdog",
            )
            logger.info("Bluetooth scanner started")

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        # Detach the idle-watchdog reference before awaiting the
        # scanner teardown -- _idle_watchdog itself calls stop() from
        # inside that very task, and cancelling the task you're
        # currently running from within would just interrupt this
        # teardown partway through.
        task = self._idle_task
        self._idle_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

        scanner = self._scanner
        self._scanner = None
        if scanner is not None:
            try:
                await scanner.stop()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Error stopping BLE scanner: %s", exc)
        logger.info("Bluetooth scanner stopped")

    def clear(self) -> None:
        self._devices.clear()

    def _on_detection(self, device, advertisement_data) -> None:
        rssi = getattr(advertisement_data, "rssi", None)
        name = getattr(advertisement_data, "local_name", None) or getattr(device, "name", None)
        now = time.time()
        existing = self._devices.get(device.address) or {}
        props = _bluez_props(device)

        manufacturer = dict(existing.get("manufacturer_data") or {})
        for company_id, payload in (getattr(advertisement_data, "manufacturer_data", None) or {}).items():
            manufacturer[str(int(company_id))] = bytes(payload).hex()
        services = list(existing.get("service_uuids") or [])
        for uuid in getattr(advertisement_data, "service_uuids", None) or []:
            if str(uuid).lower() not in services:
                services.append(str(uuid).lower())
        service_data = dict(existing.get("service_data") or {})
        for uuid, payload in (getattr(advertisement_data, "service_data", None) or {}).items():
            service_data[str(uuid).lower()] = bytes(payload).hex()
        tx_power = getattr(advertisement_data, "tx_power", None)

        self._devices[device.address] = {
            "address": device.address,
            "name": name or existing.get("name"),
            "rssi": rssi,
            "first_seen": existing.get("first_seen", now),
            "last_seen": now,
            "address_type": props.get("AddressType") or existing.get("address_type"),
            "manufacturer_data": manufacturer,
            "service_uuids": services,
            "service_data": service_data,
            "tx_power": tx_power if tx_power is not None else existing.get("tx_power"),
            "appearance": props.get("Appearance", existing.get("appearance")),
        }
        if len(self._devices) > _MAX_DEVICES:
            oldest = min(self._devices, key=lambda a: self._devices[a]["last_seen"])
            del self._devices[oldest]

    def poll(self) -> dict:
        self._last_poll_at = time.time()
        return self.status()

    def status(self) -> dict:
        devices = [_describe(d) for d in self._devices.values()]
        devices.sort(key=lambda d: d["last_seen"], reverse=True)
        return {
            "running": self.running,
            "device_count": len(devices),
            "devices": devices,
            "stale_after_seconds": _STALE_AFTER_SECONDS,
            "last_error": self._last_error,
            "vendor_db": vendor_db.status(),
        }

    async def _idle_watchdog(self) -> None:
        try:
            while True:
                await asyncio.sleep(_IDLE_CHECK_INTERVAL_SECONDS)
                if (
                    self._last_poll_at is not None
                    and time.time() - self._last_poll_at > _IDLE_STOP_SECONDS
                ):
                    logger.info(
                        "Bluetooth scanner idle for over %.0fs -- auto-stopping",
                        _IDLE_STOP_SECONDS,
                    )
                    async with self._lock:
                        await self._stop_locked()
                    return
        except asyncio.CancelledError:
            raise


def _bluez_props(device) -> dict:
    """BlueZ's D-Bus properties for this device (AddressType, Appearance,
    ...), when bleak's backend exposes them -- empty dict otherwise."""
    details = getattr(device, "details", None)
    if isinstance(details, dict):
        props = details.get("props")
        if isinstance(props, dict):
            return props
    return {}


def _describe(raw: dict) -> dict:
    """A stored row plus the human-readable names the page shows."""
    d = dict(raw)
    kind = vendor_db.address_kind(d["address"], d.get("address_type"))
    companies = []
    for company_id, payload in (d.get("manufacturer_data") or {}).items():
        cid = int(company_id)
        entry = {"id": cid, "id_hex": f"0x{cid:04X}", "name": vendor_db.company_name(cid), "data": payload}
        if cid == 0x004C:
            entry["apple_type"] = vendor_db.apple_type(payload)
        companies.append(entry)
    services = [{"uuid": u, "name": vendor_db.service_name(u)} for u in d.get("service_uuids") or []]
    for u in d.get("service_data") or {}:
        if u not in (d.get("service_uuids") or []):
            services.append({"uuid": u, "name": vendor_db.service_name(u)})
    mac_vendor = vendor_db.mac_vendor(d["address"]) if kind == "public" else None
    company = next((c["name"] for c in companies if c["name"]), None)
    d.update({
        "address_kind": kind,
        "mac_vendor": mac_vendor,
        "companies": companies,
        "services": services,
        "appearance_name": vendor_db.appearance_name(d.get("appearance")),
        # One label for the table column: the MAC vendor when the address
        # is real, else whoever the advertisement says made it.
        "vendor": mac_vendor or company,
        "vendor_source": "mac" if mac_vendor else ("company id" if company else None),
    })
    return d
