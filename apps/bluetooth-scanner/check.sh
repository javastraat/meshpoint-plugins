#!/usr/bin/env bash
#
# bluetooth-scanner plugin dependency check.
#
# Unprivileged probe run by meshpoint at boot (and on demand from
# Settings -> Plugins). Mirrors setup.sh's own idempotency checks.
#
#   exit 0        -- bluez + pi-bluetooth + bleak all present, nothing to do
#   exit non-zero -- setup needed; stdout says what's missing
#
# Doesn't check whether the adapter is actually up (rfkill/hciconfig
# state) -- that's a live runtime condition, not a "was setup run"
# one, and Start Scan already surfaces a real error from bleak/BlueZ
# if the adapter itself isn't reachable.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../../.." && pwd)"
VENV_PY="${REPO}/venv/bin/python3"

missing=()

command -v bluetoothctl &>/dev/null || missing+=("bluez")
dpkg -s pi-bluetooth &>/dev/null || missing+=("pi-bluetooth")
"${VENV_PY}" -c "import bleak" &>/dev/null || missing+=("bleak")

if [ "${#missing[@]}" -eq 0 ]; then
    echo "bluez + pi-bluetooth + bleak installed"
    exit 0
fi

echo "missing: ${missing[*]}"
echo "run: sudo meshpoint plugin setup bluetooth-scanner"
exit 1
