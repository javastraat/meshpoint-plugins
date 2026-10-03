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
    # Set by a test to make the next start() raise, simulating a real
    # bleak/BlueZ failure (e.g. adapter powered off) instead of always
    # succeeding.
    raise_on_start: BaseException | None = None

    def __init__(self, detection_callback=None):
        self.detection_callback = detection_callback
        self.started = False
        self.stopped = False
        _FakeBleakScanner.instances.append(self)

    async def start(self):
        if _FakeBleakScanner.raise_on_start is not None:
            raise _FakeBleakScanner.raise_on_start
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
        _FakeBleakScanner.raise_on_start = None  # belt-and-braces if a test's own try/finally didn't run

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

    async def test_powered_off_adapter_gets_an_actionable_error(self) -> None:
        # Real error confirmed live: bleak reaches bluetoothd over
        # D-Bus fine at this point (unlike the missing-adapter/D-Bus-
        # timeout case), but BlueZ's own adapter power state is off.
        _FakeBleakScanner.raise_on_start = RuntimeError(
            "No powered Bluetooth adapters found. Turn on Bluetooth "
            "and try again."
        )
        try:
            listener = BluetoothScannerListener()
            with self.assertRaises(RuntimeError) as ctx:
                await listener.start()
            self.assertIn("bluetoothctl power on", str(ctx.exception))
            self.assertFalse(listener.running)
        finally:
            _FakeBleakScanner.raise_on_start = None

    async def test_unrelated_start_failure_is_not_mislabeled(self) -> None:
        # Only the "power" wording gets the actionable hint appended --
        # a genuinely different failure shouldn't get a misleading
        # suggestion bolted onto it.
        _FakeBleakScanner.raise_on_start = RuntimeError("some other adapter fault")
        try:
            listener = BluetoothScannerListener()
            with self.assertRaises(RuntimeError) as ctx:
                await listener.start()
            self.assertNotIn("bluetoothctl power on", str(ctx.exception))
            self.assertIn("some other adapter fault", str(ctx.exception))
        finally:
            _FakeBleakScanner.raise_on_start = None

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

    async def test_stale_devices_are_kept_for_the_page_to_filter(self) -> None:
        """The backend no longer drops devices unseen for 2 minutes: a
        stopped scan keeps its last table, and the page's "Hide devices not
        seen for 2 min" checkbox does the filtering while scanning."""
        listener = BluetoothScannerListener()
        listener._devices["stale"] = {
            "address": "stale", "name": None, "rssi": -50,
            "first_seen": 0.0, "last_seen": 0.0,
        }
        listener._devices["fresh"] = {
            "address": "fresh", "name": None, "rssi": -50,
            "first_seen": time.time(), "last_seen": time.time(),
        }
        status = listener.status()
        self.assertEqual({d["address"] for d in status["devices"]}, {"stale", "fresh"})
        self.assertEqual(status["stale_after_seconds"], 120.0)

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


class _FakeAdv:
    def __init__(self, rssi=-60, local_name=None, manufacturer_data=None,
                 service_uuids=None, service_data=None, tx_power=None):
        self.rssi = rssi
        self.local_name = local_name
        self.manufacturer_data = manufacturer_data or {}
        self.service_uuids = service_uuids or []
        self.service_data = service_data or {}
        self.tx_power = tx_power


class _BlueZDevice(_FakeDevice):
    def __init__(self, address, name=None, props=None):
        super().__init__(address, name)
        self.details = {"path": "/org/bluez/hci0/dev_x", "props": props or {}}


class TestAdvertisementDetails(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        from backend import vendor_db
        self.vendor_db = vendor_db
        self._tmp = tempfile.TemporaryDirectory()
        self._saved_dir = vendor_db.DATA_DIR
        vendor_db.DATA_DIR = Path(self._tmp.name)
        vendor_db.reload()

    def tearDown(self) -> None:
        self.vendor_db.DATA_DIR = self._saved_dir
        self.vendor_db.reload()
        self._tmp.cleanup()

    def _write_db(self) -> None:
        import json
        d = self.vendor_db.DATA_DIR
        macs = self.vendor_db.parse_mac_csv(
            "Mac Prefix,Vendor Name,Private,Block Type,Last Update\n"
            "44:1B:F6,Espressif Inc.,false,MA-L,2026/06/05\n"
            "A0:02:4A,IEEE Registration Authority,false,MA-L,2023/03/10\n"
            "A0:02:4A:9,Kontakt Micro-Location Sp z o.o.,false,MA-M,2020/11/07\n"
        )
        (d / "macs.json").write_text(json.dumps(macs))
        (d / "companies.json").write_text(json.dumps({"76": "Apple, Inc.", "1234": "Acme"}))
        (d / "services.json").write_text(json.dumps({"180d": "Heart Rate"}))
        (d / "meta.json").write_text(json.dumps({"updated_at": 1.0, "counts": {"mac_prefixes": 3}}))
        self.vendor_db.reload()

    def test_address_kind(self) -> None:
        k = self.vendor_db.address_kind
        self.assertEqual(k("44:1B:F6:69:D5:F1"), "public")
        self.assertEqual(k("77:FA:BE:C9:F8:16"), "random (resolvable private)")
        # Bit 0x02 clear + top bits 11: without a vendor DB it's taken as
        # public (can't tell) ...
        self.assertEqual(k("C0:28:8D:8E:B8:DF"), "public")
        self.assertEqual(k("C0:28:8D:8E:B8:DF", "random"), "random static")
        self.assertEqual(k("32:DE:00:84:5C:FA"), "random (non-resolvable)")
        # BlueZ's own AddressType wins over the bit heuristic.
        self.assertEqual(k("44:1B:F6:69:D5:F1", "random"), "random (resolvable private)")  # 0x44 = 01xxxxxx
        self.assertEqual(k("77:FA:BE:C9:F8:16", "public"), "public")

    def test_address_kind_uses_vendor_db_for_ambiguous_addresses(self) -> None:
        self._write_db()
        k = self.vendor_db.address_kind
        # ... with one, an unknown prefix with top bits 11 is random static,
        # a known vendor prefix stays public.
        self.assertEqual(k("D1:22:33:44:55:66"), "random static")
        self.assertEqual(k("44:1B:F6:69:D5:F1"), "public")

    def test_mac_vendor_longest_prefix_wins(self) -> None:
        self._write_db()
        self.assertEqual(self.vendor_db.mac_vendor("A0:02:4A:9A:8E:AC"), "Kontakt Micro-Location Sp z o.o.")
        self.assertEqual(self.vendor_db.mac_vendor("A0:02:4A:1A:00:00"), "IEEE Registration Authority")
        self.assertEqual(self.vendor_db.mac_vendor("44:1B:F6:69:D5:F1"), "Espressif Inc.")
        self.assertIsNone(self.vendor_db.mac_vendor("12:34:56:78:9A:BC"))

    def test_names_fall_back_to_builtin_without_db(self) -> None:
        self.assertFalse(self.vendor_db.status()["present"])
        self.assertEqual(self.vendor_db.company_name(0x004C), "Apple, Inc.")
        self.assertEqual(self.vendor_db.service_name("0000180d-0000-1000-8000-00805f9b34fb"), "Heart Rate")
        self.assertEqual(self.vendor_db.service_name("6BA1B218-15A8-461F-9FA8-5DCAE273EAFD"), "Meshtastic")
        self.assertEqual(self.vendor_db.appearance_name(0x00C1), "Watch")   # category 3
        self.assertEqual(self.vendor_db.apple_type("1205aabb"), "Find My")

    def test_detection_merges_adverts_and_describes(self) -> None:
        self._write_db()
        listener = BluetoothScannerListener()
        dev = _BlueZDevice("44:1B:F6:69:D5:F1", props={"AddressType": "public", "Appearance": 0x00C1})
        listener._on_detection(dev, _FakeAdv(local_name="RNode 2726", manufacturer_data={1234: b"\x01\x02"}))
        # A scan response with only service info must not wipe the name/mfr data.
        listener._on_detection(dev, _FakeAdv(service_uuids=["0000180D-0000-1000-8000-00805F9B34FB"],
                                             service_data={"0000180d-0000-1000-8000-00805f9b34fb": b"\xff"},
                                             tx_power=4))
        d = listener.status()["devices"][0]
        self.assertEqual(d["name"], "RNode 2726")
        self.assertEqual(d["address_kind"], "public")
        self.assertEqual(d["mac_vendor"], "Espressif Inc.")
        self.assertEqual(d["vendor"], "Espressif Inc.")
        self.assertEqual(d["vendor_source"], "mac")
        self.assertEqual(d["companies"], [{"id": 1234, "id_hex": "0x04D2", "name": "Acme", "data": "0102"}])
        self.assertEqual(d["services"], [{"uuid": "0000180d-0000-1000-8000-00805f9b34fb", "name": "Heart Rate"}])
        self.assertEqual(d["service_data"], {"0000180d-0000-1000-8000-00805f9b34fb": "ff"})
        self.assertEqual(d["tx_power"], 4)
        self.assertEqual(d["appearance_name"], "Watch")

    def test_random_address_named_by_company_not_mac(self) -> None:
        self._write_db()
        listener = BluetoothScannerListener()
        listener._on_detection(_FakeDevice("77:FA:BE:C9:F8:16"),
                               _FakeAdv(manufacturer_data={0x004C: bytes.fromhex("1005")}))
        d = listener.status()["devices"][0]
        self.assertIsNone(d["mac_vendor"])
        self.assertEqual(d["vendor"], "Apple, Inc.")
        self.assertEqual(d["vendor_source"], "company id")
        self.assertEqual(d["companies"][0]["apple_type"], "Nearby Info")

    def test_device_cap_drops_least_recently_seen(self) -> None:
        from backend import listener as listener_mod
        saved = listener_mod._MAX_DEVICES
        listener_mod._MAX_DEVICES = 2
        try:
            lis = BluetoothScannerListener()
            for i, addr in enumerate(["00:00:00:00:00:01", "00:00:00:00:00:02", "00:00:00:00:00:03"]):
                lis._on_detection(_FakeDevice(addr), _FakeAdv())
                lis._devices[addr]["last_seen"] = float(i)
            self.assertEqual(set(lis._devices), {"00:00:00:00:00:02", "00:00:00:00:00:03"})
        finally:
            listener_mod._MAX_DEVICES = saved

    def test_parsers_on_sig_shapes(self) -> None:
        companies = self.vendor_db.parse_companies("company_identifiers:\n  - value: 0x004C\n    name: 'Apple, Inc.'\n")
        self.assertEqual(companies, {"76": "Apple, Inc."})
        services = self.vendor_db.parse_services("uuids:\n - uuid: 0x180D\n   name: Heart Rate\n   id: x\n",
                                                 "uuids:\n - uuid: 0xFEED\n   name: \"Tile, Inc.\"\n")
        self.assertEqual(services, {"180d": "Heart Rate", "feed": "Tile, Inc."})
        appearance = self.vendor_db.parse_appearance("appearance_values:\n  - category: 0x003\n    name: Watch\n")
        self.assertEqual(appearance, {"3": "Watch"})


if __name__ == "__main__":
    unittest.main()
