#!/usr/bin/env bash
#
# bluetooth-scanner plugin setup: installs bleak (async BLE scanning,
# talks to BlueZ over D-Bus on Linux) into Meshpoint's own venv, and
# makes sure the whole BlueZ stack this needs is actually installed,
# enabled, and unblocked -- not just the bare `bluez` package.
#
# On a real Pi 4, `apt install bluez` alone is NOT enough: the onboard
# adapter is UART-attached and needs the Raspberry-Pi-specific
# `pi-bluetooth` package (wires it up via hciuart.service) too, or
# `hciconfig` reports "Can't get device info: No such device" even
# with bluetoothd installed and running. Confirmed live on this same
# hardware during this plugin's own development -- without it, `Start
# scan` fails with:
#   BleakDBusError: Failed to activate service 'org.bluez': timed out
# (D-Bus tries to on-demand-start bluetoothd, which either isn't
# enabled yet or has nothing to bind to without pi-bluetooth, and
# gives up after its own 25s timeout).
#
# Idempotent: skips whichever piece is already satisfied.
#
# Run once, admin-triggered from Settings -> Plugins "Run setup", or:
#     sudo bash plugins/apps/bluetooth-scanner/setup.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../../.." && pwd)"
VENV_PIP="${REPO}/venv/bin/pip"
VENV_PY="${REPO}/venv/bin/python3"

needs_reboot=0

if command -v bluetoothctl &>/dev/null; then
    echo "bluez already installed -- skipping"
else
    echo "Installing bluez ..."
    apt-get update -qq
    apt-get install -y -qq bluez
fi

# Raspberry Pi OS only -- dpkg -s (not command -v) since this package
# has no binary of its own, just udev rules + a systemd unit.
if dpkg -s pi-bluetooth &>/dev/null; then
    echo "pi-bluetooth already installed -- skipping"
else
    echo "Installing pi-bluetooth (Raspberry Pi onboard BT support) ..."
    apt-get install -y -qq raspberrypi-sys-mods pi-bluetooth wireless-regdb
    needs_reboot=1
fi

# A fresh `apt install bluez` doesn't always leave bluetoothd started
# -- make sure it actually is, so bleak's first D-Bus call doesn't hit
# an on-demand-activation timeout.
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

if [ "${needs_reboot}" = "1" ]; then
    echo ""
    echo "pi-bluetooth was just installed -- hciuart.service binds to the"
    echo "UART device at boot, not on a plain service (re)start, so a"
    echo "REBOOT is required before the onboard adapter actually comes up:"
    echo "    sudo reboot"
fi
