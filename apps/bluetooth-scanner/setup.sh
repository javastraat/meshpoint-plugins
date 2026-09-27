#!/usr/bin/env bash
#
# bluetooth-scanner plugin setup: installs bleak (async BLE scanning,
# talks to BlueZ over D-Bus on Linux) into Meshpoint's own venv, and
# makes sure the whole BlueZ stack this needs is actually installed,
# enabled, and unblocked -- not just the bare `bluez` package.
#
# On a real Pi 4, `apt install bluez` alone is NOT enough: the onboard
# adapter is UART-attached and needs the Raspberry-Pi-specific
# `pi-bluetooth` package too, or `hciconfig` reports "Can't get device
# info: No such device" even with bluetoothd installed and running --
# confirmed live on this same hardware (bluez alone, pre-pi-bluetooth,
# left hciconfig failing exactly that way). Without it, `Start scan`
# fails with:
#   BleakDBusError: Failed to activate service 'org.bluez': timed out
#
# Also checks for an ACTIVE `dtoverlay=disable-bt` in config.txt --
# some GPS/UART troubleshooting on this same class of board tries
# freeing the primary UART by disabling the onboard BT adapter
# entirely at the device-tree level. That's invisible to every check
# above (bluez/pi-bluetooth/bleak can all be perfectly installed and
# it'll still fail identically) since it disables the hardware itself,
# not a userspace package or service. Comments it back out if found --
# never touches an already-commented line.
#
# Idempotent: skips whichever piece is already satisfied. Two-phase:
# stops right after fixing config.txt / installing pi-bluetooth and
# asks for a single combined reboot + a second run, rather than
# pressing on to enable/unblock in the same pass. Honest caveat: the
# one real verified working sequence for the pi-bluetooth half
# involved a reboot, but that same reboot was ALSO picking up an
# unrelated config.txt edit made around the same time -- so it isn't
# fully isolated proof that pi-bluetooth alone requires a reboot, just
# that rebooting is what's actually been tested to work. Recommending
# it anyway since it's low-cost either way; a config.txt change never
# takes effect without one regardless.
#
# Run once, admin-triggered from Settings -> Plugins "Run setup", or:
#     sudo bash plugins/apps/bluetooth-scanner/setup.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../../.." && pwd)"
VENV_PIP="${REPO}/venv/bin/pip"
VENV_PY="${REPO}/venv/bin/python3"

needs_reboot=0

# ---- config.txt: make sure the onboard adapter isn't disabled outright ----
CONFIG_TXT=""
for candidate in /boot/firmware/config.txt /boot/config.txt; do
    if [ -f "$candidate" ]; then
        CONFIG_TXT="$candidate"
        break
    fi
done

if [ -n "$CONFIG_TXT" ] && grep -qE '^[[:space:]]*dtoverlay=disable-bt([[:space:]]|$)' "$CONFIG_TXT"; then
    echo "Found an ACTIVE 'dtoverlay=disable-bt' in $CONFIG_TXT -- this"
    echo "disables the onboard Bluetooth adapter at the hardware level,"
    echo "which breaks this plugin regardless of any package/service state."
    echo "Commenting it out."
    sed -i -E 's/^([[:space:]]*)dtoverlay=disable-bt([[:space:]]|$)/\1#dtoverlay=disable-bt\2/' "$CONFIG_TXT"
    needs_reboot=1
fi

# ---- bluez itself ----
if command -v bluetoothctl &>/dev/null; then
    echo "bluez already installed -- skipping"
else
    echo "Installing bluez ..."
    apt-get update -qq
    apt-get install -y -qq bluez
fi

# ---- pi-bluetooth: Raspberry Pi OS only -- dpkg -s (not command -v) ----
# since this package has no binary of its own, just udev rules + a
# systemd unit.
if ! dpkg -s pi-bluetooth &>/dev/null; then
    echo "Installing pi-bluetooth (Raspberry Pi onboard BT support) ..."
    apt-get install -y -qq raspberrypi-sys-mods pi-bluetooth wireless-regdb
    needs_reboot=1
else
    echo "pi-bluetooth already installed -- skipping"
fi

# ---- bleak: install now regardless, so a post-reboot re-run doesn't ----
# need to redo this step too.
if "${VENV_PY}" -c "import bleak" &>/dev/null; then
    echo "bleak already installed -- skipping"
else
    echo "Installing bleak into the venv ..."
    "${VENV_PIP}" install --quiet 'bleak>=0.21'
fi

if [ "$needs_reboot" = "1" ]; then
    echo ""
    echo "A REBOOT is recommended before continuing -- then run setup again"
    echo "to finish enabling and unblocking the adapter:"
    echo "    sudo reboot"
    echo "    sudo meshpoint plugin setup bluetooth-scanner"
    exit 0
fi

# Only reached once config.txt is already clean AND pi-bluetooth is
# already in place (a prior run of this same script, or already
# present on this image), so hci0 genuinely exists for these to act on.
#
# A fresh `apt install bluez` doesn't always leave bluetoothd started
# -- make sure it actually is, so bleak's first D-Bus call doesn't hit
# an on-demand-activation timeout.
systemctl enable --now bluetooth 2>/dev/null || true

# RF-kill soft-blocks the radio by default on some images/board revs.
if command -v rfkill &>/dev/null; then
    rfkill unblock bluetooth 2>/dev/null || true
fi

echo "bluetooth-scanner setup complete."
