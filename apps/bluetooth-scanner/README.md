# Bluetooth Scanner plugin

A generic nearby-BLE-device radar: **Start scan** / **Stop scan**, a live
table of every advertising Bluetooth LE device in range (address, name,
RSSI, last seen), sortable by any column by clicking its header. Click a
row for a right-side detail drawer — same visual system as the
LoRaWAN/Meshtastic/MeshCore/Reticulum protocol pages (their shared
`lw-*` table/panel and `nd-*` drawer CSS classes, reused directly rather
than a hand-copied approximation — same approach the Reticulum plugin's
own Peers drawer already takes).

Adds "Bluetooth Scanner" under the **Networks** section of the sidebar.
Uses the Pi's own onboard (or a USB) Bluetooth adapter via
[`bleak`](https://github.com/hbldh/bleak) — nothing else in Meshpoint
touches Bluetooth, so there's no contention with the SX1302 concentrator,
MeshCore companions, or the RTL-SDR family of plugins.

This is intentionally a **generic** scanner, not a MeshCore-BLE-tag
correlator — it just shows whatever's advertising nearby, the same way a
phone's Bluetooth settings page would, with a persistent live table instead
of a one-shot list.

## Enable it

```yaml
plugins:
  bluetooth-scanner:
    enabled: true
```

Run setup first (installs `bluez` if missing, and `bleak` into Meshpoint's
venv) — either **Settings → Plugins → Run setup**, or over SSH:

```bash
sudo meshpoint plugin setup bluetooth-scanner
```

Restart, then **Networks → Bluetooth Scanner** in the sidebar.

## Notes

- **Idle auto-stop.** A scan left running with nobody watching the page
  auto-stops after 10 minutes of no `/status` polls — a forgotten open tab
  shouldn't keep the adapter scanning (and draining a little extra power)
  forever.
- **Stale-device pruning.** A device that stops re-advertising drops off
  the table after 2 minutes, so what's shown is "still in range right now,"
  not "everything ever seen since Start was clicked."
- **RF-kill / adapter not up.** If the onboard adapter is blocked (common
  on a fresh Pi image), Start will fail with whatever error `bleak`/BlueZ
  reports. Check with `rfkill list` and `sudo rfkill unblock bluetooth`,
  then `sudo hciconfig hci0 up` to confirm the adapter itself comes up
  before troubleshooting the plugin.

## Layout

```
plugin.toml                       manifest ([sidebar] + [deps] + [frontend])
setup.sh / check.sh                apt bluez + venv pip install bleak
backend/__init__.py                register(reg) -- add_router + add_listener
backend/listener.py                BluetoothScannerListener (bleak-backed, FastAPI-free)
backend/routes.py                  /api/bluetooth-scanner/{status,start,stop,clear}
backend/tests/test_listener.py     stubs bleak via sys.modules -- no real adapter needed
frontend/bluetooth_scanner.js      the sidebar page (registerSidebarPage) + poll loop +
                                    a small detail drawer (own markup/data, core nd-* CSS)
frontend/bluetooth_scanner.css     only what core CSS doesn't already cover: this
                                    table's column widths + sortable-header indicators
```

Full plugin-architecture write-up: [docs/PLUGINS.md](https://github.com/KMX415/meshpoint/blob/main/docs/PLUGINS.md)
(in the main Meshpoint repo — this satellite repo has no `docs/` of its own).
