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
# Idempotent: skips whichever piece is already satisfied. Two-phase:
# stops right after installing pi-bluetooth and asks for a reboot +
# a second run, rather than pressing on to enable/unblock in the same
# pass. Honest caveat: the one real verified working sequence for this
# involved a reboot, but that same reboot was ALSO picking up an
# unrelated /boot/firmware/config.txt edit made around the same time --
# so it isn't fully isolated proof that pi-bluetooth alone requires a
# reboot, just that rebooting is what's actually been tested to work.
# Recommending it anyway since it's low-cost either way.
#
# Run once, admin-triggered from Settings -> Plugins "Run setup", or:
#     sudo bash plugins/apps/bluetooth-scanner/setup.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../../.." && pwd)"
VENV_PIP="${REPO}/venv/bin/pip"
VENV_PY="${REPO}/venv/bin/python3"

if command -v bluetoothctl &>/dev/null; then
    echo "bluez already installed -- skipping"
else
    echo "Installing bluez ..."
    apt-get update -qq
    apt-get install -y -qq bluez
fi

# Raspberry Pi OS only -- dpkg -s (not command -v) since this package
# has no binary of its own, just udev rules + a systemd unit.
if ! dpkg -s pi-bluetooth &>/dev/null; then
    echo "Installing pi-bluetooth (Raspberry Pi onboard BT support) ..."
    apt-get install -y -qq raspberrypi-sys-mods pi-bluetooth wireless-regdb

    if "${VENV_PY}" -c "import bleak" &>/dev/null; then
        echo "bleak already installed -- skipping"
    else
        echo "Installing bleak into the venv ..."
        "${VENV_PIP}" install --quiet 'bleak>=0.21'
    fi

    echo ""
    echo "pi-bluetooth just installed. A REBOOT is recommended before hci0"
    echo "reliably exists -- then run setup again to finish enabling and"
    echo "unblocking the adapter:"
    echo "    sudo reboot"
    echo "    sudo meshpoint plugin setup bluetooth-scanner"
    exit 0
fi
echo "pi-bluetooth already installed -- skipping"

# Only reached once pi-bluetooth is already in place (a prior run of
# this same script, or already present on this image).
#
# A fresh `apt install bluez` doesn't always leave bluetoothd started
# -- make sure it actually is, so bleak's first D-Bus call doesn't hit
# an on-demand-activation timeout (BleakDBusError: Failed to activate
# service 'org.bluez': timed out).
systemctl enable --now bluetooth 2>/dev/null || true

# RF-kill soft-blocks the radio by default on some images/board revs.
if command -v rfkill &>/dev/null; then
    rfkill unblock bluetooth 2>/dev/null || true
fi

if "${VENV_PY}" -c "import bleak" &>/dev/null; then
    echo "bleak already installed -- skipping"
else
    echo "Installing bleak into the venv ..."
    "${VENV_PIP}" install --quiet 'bleak>=0.21'
fi

echo "bluetooth-scanner setup complete."
