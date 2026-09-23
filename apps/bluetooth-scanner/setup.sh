#!/usr/bin/env bash
#
# bluetooth-scanner plugin setup: installs bleak (async BLE scanning,
# talks to BlueZ over D-Bus on Linux) into Meshpoint's own venv, and
# makes sure the bluez system package -- and therefore bluetoothctl,
# the adapter, and the D-Bus BlueZ service bleak needs -- is present.
#
# Idempotent: skips whichever half is already satisfied.
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

if "${VENV_PY}" -c "import bleak" &>/dev/null; then
    echo "bleak already installed -- skipping"
else
    echo "Installing bleak into the venv ..."
    "${VENV_PIP}" install --quiet 'bleak>=0.21'
fi

echo "bluetooth-scanner setup complete."
