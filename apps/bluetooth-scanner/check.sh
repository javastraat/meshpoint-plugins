#!/usr/bin/env bash
#
# bluetooth-scanner plugin dependency check.
#
# Unprivileged probe run by meshpoint at boot (and on demand from
# Settings -> Plugins). Mirrors setup.sh's own idempotency checks.
#
#   exit 0        -- bluez + bleak both present, nothing to do
#   exit non-zero -- setup needed; stdout says what's missing
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../../.." && pwd)"
VENV_PY="${REPO}/venv/bin/python3"

missing=()

command -v bluetoothctl &>/dev/null || missing+=("bluez")
"${VENV_PY}" -c "import bleak" &>/dev/null || missing+=("bleak")

if [ "${#missing[@]}" -eq 0 ]; then
    echo "bluez + bleak installed"
    exit 0
fi

echo "missing: ${missing[*]}"
echo "run: sudo meshpoint plugin setup bluetooth-scanner"
exit 1
