#!/usr/bin/env bash
#
# oled-display plugin dependency check.
#
# Unprivileged probe run by meshpoint at boot (and on demand from
# Settings -> Plugins). Mirrors setup.sh's own idempotency check.
#
#   exit 0        -- luma.oled + pillow + httpx importable, nothing to do
#   exit non-zero -- setup needed; stdout says what's missing
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../../.." && pwd)"
VENV_PY="${REPO}/venv/bin/python3"

if "${VENV_PY}" -c "import luma.oled, PIL, httpx" &>/dev/null; then
    echo "luma.oled + pillow + httpx installed"
    exit 0
fi

echo "luma.oled + pillow + httpx not installed yet"
echo "run: sudo meshpoint plugin setup oled-display"
exit 1
