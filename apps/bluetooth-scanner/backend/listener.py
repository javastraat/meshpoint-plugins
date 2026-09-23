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

logger = logging.getLogger(__name__)

# Drop a device from the table if it hasn't re-advertised in this long
# -- keeps the list to what's actually still in range, not a growing
# history of everything ever seen since Start was clicked.
_STALE_AFTER_SECONDS = 120.0

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
                self._last_error = f"{type(exc).__name__}: {exc}"
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
        self._devices[device.address] = {
            "address": device.address,
            "name": name,
            "rssi": rssi,
            "last_seen": time.time(),
        }

    def poll(self) -> dict:
        self._last_poll_at = time.time()
        return self.status()

    def status(self) -> dict:
        now = time.time()
        devices = [
            d for d in self._devices.values()
            if now - d["last_seen"] <= _STALE_AFTER_SECONDS
        ]
        devices.sort(key=lambda d: d["last_seen"], reverse=True)
        return {
            "running": self.running,
            "device_count": len(devices),
            "devices": devices,
            "last_error": self._last_error,
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
