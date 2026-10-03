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

Run setup first — either **Settings → Plugins → Run setup**, or over SSH:

```bash
sudo meshpoint plugin setup bluetooth-scanner
```

This installs `bluez`, the Raspberry-Pi-specific `pi-bluetooth` package
(the onboard adapter is UART-attached and needs it specifically — plain
`bluez` alone leaves `hciconfig` reporting "no such device"), enables and
starts the `bluetooth` service, unblocks rfkill, and installs `bleak` into
Meshpoint's venv. It also checks `config.txt` for an *active*
`dtoverlay=disable-bt` line — some GPS/UART troubleshooting on this class
of board frees the primary UART by disabling the onboard BT adapter
entirely at the device-tree level, which is invisible to every other
check here (packages can all be installed correctly and it'll still fail
identically) since it disables the hardware itself. Comments it back out
if found; never touches an already-commented line.

**If `pi-bluetooth` was just installed, or an active `disable-bt` line
was just commented out**, setup stops there and asks for a reboot, then
run it again:

```bash
sudo reboot
sudo meshpoint plugin setup bluetooth-scanner
```

The one real tested-working sequence for this included a reboot at that
point, so it's the recommended path — though that same reboot also
happened to pick up an unrelated config.txt edit at the same time, so
it isn't fully isolated proof pi-bluetooth alone requires one. Rebooting
is low-cost either way.

Restart, then **Networks → Bluetooth Scanner** in the sidebar.

## Notes

- **Idle auto-stop.** A scan left running with nobody watching the page
  auto-stops after 10 minutes of no `/status` polls — a forgotten open tab
  shouldn't keep the adapter scanning (and draining a little extra power)
  forever.
- **Stale-device pruning.** A device that stops re-advertising drops off
  the table after 2 minutes, so what's shown is "still in range right now,"
  not "everything ever seen since Start was clicked."
- **`BleakDBusError: Failed to activate service 'org.bluez': timed out`**
  on Start scan means bluetoothd itself never came up — confirmed on a
  genuinely fresh Pi where `pi-bluetooth` had never been installed
  before. Re-running setup (above) should now catch and fix this.
- **`BleakBluetoothNotAvailableError: No powered Bluetooth adapters
  found` (`POWERED_OFF`)** means bluetoothd is reachable but the
  adapter's own BlueZ-level power state is off — a genuinely separate
  thing from rfkill or `hciconfig up`. Confirmed live: the actual root
  cause turned out to be `rfkill list bluetooth` still reporting `Soft
  blocked: yes` even after installing `pi-bluetooth` and rebooting —
  bluetoothd's own journal showed `Failed to set mode: Failed (0x03)`
  on every power-on attempt while that block was in place. Setup now
  unblocks rfkill and explicitly runs `bluetoothctl power on`, in that
  order (`org.bluez.Error.Busy` from the power-on step immediately
  after is harmless — BlueZ auto-powers the adapter itself right after
  an rfkill unblock and can race the script's own explicit attempt).
  If Start scan still fails after re-running setup, check `rfkill list
  bluetooth` directly to confirm it now says `Soft blocked: no`.

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

## Vendor and device info

Each device gets a **Vendor** column and, in its side panel, an
**Identification** section (MAC vendor, manufacturer, appearance, known
services) and an **Advertisement** section (TX power, raw manufacturer
data, service UUIDs and service data).

- **MAC vendor** only works for *public* addresses. Many BLE devices
  (phones, watches, earbuds, trackers) advertise from a random, rotating
  address with no vendor prefix; the panel's **Address type** says so.
- **Manufacturer** comes from the company ID inside the advertisement
  (Apple, Microsoft, Samsung, ...), so it works for random addresses too.
  For Apple it also shows the Continuity type (Find My, AirPods, Nearby
  Info, ...).
- **Services** are named from the Bluetooth SIG lists plus a few
  well-known 128-bit UUIDs (Meshtastic, Nordic UART as used by RNode and
  MeshCore).

The names come from an offline database in `data/bluetooth-scanner/`:
the [maclookup.app](https://maclookup.app/downloads/csv-database) MAC
vendor CSV and the Bluetooth SIG's company identifiers, service UUIDs and
appearance values (about 2 MB). `setup.sh` downloads it; **Refresh** on
the page (admin) downloads it again. Without it, a small built-in list
still names the most common companies and services.

## Keeping the table

**Hide devices not seen for 2 min** (on by default, remembered per
browser) hides devices that stopped advertising while a scan runs. After
**Stop scan** the table keeps its last state either way, until **Clear**.
With the box off, everything seen during the scan stays (capped at 2,000
devices).
