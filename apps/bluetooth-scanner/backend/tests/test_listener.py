"""Tests for BluetoothScannerListener.

bleak talks to BlueZ over D-Bus -- not importable/usable on a Mac dev
box or in CI without real Bluetooth hardware, so it's stubbed via
sys.modules the same way this repo's other plugins stub their own
hardware-only dependencies for Mac-side testing. listener.py imports
bleak lazily inside start() specifically so this works: the stub just
needs to be in place by the time a test calls start(), not at import
time.
"""

from __future__ import annotations

import sys
import time
import types
import unittest
from pathlib import Path

# Self-sufficient regardless of cwd/pytest rootdir: resolve the plugin
# root (two levels up from this file) and import via the real
# `backend` package rather than guessing a relative path.
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from backend.listener import BluetoothScannerListener  # noqa: E402


class _FakeBleakScanner:
    """Records start()/stop() calls and stashes the detection_callback
    so a test can fire fake advertisements through it."""

    instances: list["_FakeBleakScanner"] = []

    def __init__(self, detection_callback=None):
        self.detection_callback = detection_callback
        self.started = False
        self.stopped = False
        _FakeBleakScanner.instances.append(self)

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


class _FakeDevice:
    def __init__(self, address, name=None):
        self.address = address
        self.name = name


class _FakeAdvertisementData:
    def __init__(self, rssi=None, local_name=None):
        self.rssi = rssi
        self.local_name = local_name


def _install_fake_bleak():
    _FakeBleakScanner.instances = []
    fake_module = types.ModuleType("bleak")
    fake_module.BleakScanner = _FakeBleakScanner
    sys.modules["bleak"] = fake_module


class TestBluetoothScannerListener(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _install_fake_bleak()

    def tearDown(self) -> None:
        sys.modules.pop("bleak", None)

    async def test_not_running_initially(self) -> None:
        listener = BluetoothScannerListener()
        self.assertFalse(listener.running)
        status = listener.status()
        self.assertFalse(status["running"])
        self.assertEqual(status["device_count"], 0)
        self.assertEqual(status["devices"], [])
        self.assertIsNone(status["last_error"])

    async def test_start_creates_and_starts_a_scanner(self) -> None:
        listener = BluetoothScannerListener()
        await listener.start()
        try:
            self.assertTrue(listener.running)
            self.assertEqual(len(_FakeBleakScanner.instances), 1)
            self.assertTrue(_FakeBleakScanner.instances[0].started)
        finally:
            await listener.stop()

    async def test_start_is_idempotent(self) -> None:
        listener = BluetoothScannerListener()
        await listener.start()
        try:
            await listener.start()  # must not raise or create a second scanner
            self.assertEqual(len(_FakeBleakScanner.instances), 1)
        finally:
            await listener.stop()

    async def test_stop_before_start_is_a_noop(self) -> None:
        listener = BluetoothScannerListener()
        await listener.stop()  # must not raise
        self.assertFalse(listener.running)

    async def test_stop_tears_down_the_scanner(self) -> None:
        listener = BluetoothScannerListener()
        await listener.start()
        scanner = _FakeBleakScanner.instances[0]
        await listener.stop()
        self.assertFalse(listener.running)
        self.assertTrue(scanner.stopped)

    async def test_missing_bleak_raises_runtime_error(self) -> None:
        # `sys.modules["bleak"] = None` is the standard idiom to force
        # ImportError regardless of whether bleak is actually
        # installed in this interpreter -- plain sys.modules.pop()
        # isn't enough, since Python would just re-import the real
        # package fresh (and it genuinely is installed on this Mac).
        sys.modules["bleak"] = None
        listener = BluetoothScannerListener()
        with self.assertRaises(RuntimeError) as ctx:
            await listener.start()
        self.assertIn("bleak is not installed", str(ctx.exception))
        self.assertFalse(listener.running)

    async def test_detection_callback_populates_devices(self) -> None:
        listener = BluetoothScannerListener()
        await listener.start()
        try:
            scanner = _FakeBleakScanner.instances[0]
            scanner.detection_callback(
                _FakeDevice("AA:BB:CC:DD:EE:FF", name="cached-name"),
                _FakeAdvertisementData(rssi=-67, local_name="Advertised Name"),
            )
            status = listener.status()
            self.assertEqual(status["device_count"], 1)
            device = status["devices"][0]
            self.assertEqual(device["address"], "AA:BB:CC:DD:EE:FF")
            # Advertised local_name wins over the device's cached name.
            self.assertEqual(device["name"], "Advertised Name")
            self.assertEqual(device["rssi"], -67)
        finally:
            await listener.stop()

    async def test_detection_falls_back_to_device_name(self) -> None:
        listener = BluetoothScannerListener()
        await listener.start()
        try:
            scanner = _FakeBleakScanner.instances[0]
            scanner.detection_callback(
                _FakeDevice("11:22:33:44:55:66", name="Only Device Name"),
                _FakeAdvertisementData(rssi=-80, local_name=None),
            )
            device = listener.status()["devices"][0]
            self.assertEqual(device["name"], "Only Device Name")
        finally:
            await listener.stop()

    async def test_first_seen_stays_stable_across_repeated_detections(self) -> None:
        listener = BluetoothScannerListener()
        await listener.start()
        try:
            scanner = _FakeBleakScanner.instances[0]
            scanner.detection_callback(
                _FakeDevice("AA:AA:AA:AA:AA:AA"), _FakeAdvertisementData(rssi=-70),
            )
            first_seen = listener.status()["devices"][0]["first_seen"]

            # A device that keeps re-advertising should keep its ORIGINAL
            # first_seen even as last_seen/rssi update on every detection.
            scanner.detection_callback(
                _FakeDevice("AA:AA:AA:AA:AA:AA"), _FakeAdvertisementData(rssi=-65),
            )
            device = listener.status()["devices"][0]
            self.assertEqual(device["first_seen"], first_seen)
            self.assertEqual(device["rssi"], -65)
        finally:
            await listener.stop()

    async def test_stale_devices_are_filtered_out(self) -> None:
        listener = BluetoothScannerListener()
        listener._devices["stale"] = {
            "address": "stale",
            "name": None,
            "rssi": -50,
            "first_seen": 0.0,
            "last_seen": 0.0,  # epoch -- guaranteed far older than the staleness window
        }
        listener._devices["fresh"] = {
            "address": "fresh",
            "name": None,
            "rssi": -50,
            "first_seen": time.time(),
            "last_seen": time.time(),
        }
        status = listener.status()
        addresses = {d["address"] for d in status["devices"]}
        self.assertEqual(addresses, {"fresh"})

    async def test_clear_empties_the_table(self) -> None:
        listener = BluetoothScannerListener()
        listener._devices["x"] = {
            "address": "x", "name": None, "rssi": -50,
            "first_seen": time.time(), "last_seen": time.time(),
        }
        self.assertEqual(listener.status()["device_count"], 1)
        listener.clear()
        self.assertEqual(listener.status()["device_count"], 0)

    async def test_poll_updates_last_poll_at(self) -> None:
        listener = BluetoothScannerListener()
        self.assertIsNone(listener._last_poll_at)
        listener.poll()
        self.assertIsNotNone(listener._last_poll_at)


if __name__ == "__main__":
    unittest.main()
