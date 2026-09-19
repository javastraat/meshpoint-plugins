#!/usr/bin/env bash
#
# oled-display plugin setup: installs luma.oled + pillow + httpx into
# Meshpoint's own venv -- same pattern as the Reticulum plugin's own
# setup.sh (a plugin-specific pip dep goes into the shared venv, not a
# separate one). httpx is for a best-effort local status check against
# other plugins' own APIs (e.g. Reticulum's, which -- being a `service`
# not a CaptureSource -- would otherwise never show up on the display).
#
# No system apt packages needed: luma.oled pulls in its own pure-Python
# smbus2 dependency for I2C, no python3-smbus/i2c-tools required.
#
# Idempotent: skips the install if already present.
#
# Run once, admin-triggered from Settings -> Plugins "Run setup", or:
#     sudo bash plugins/apps/oled-display/setup.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../../.." && pwd)"
VENV_PIP="${REPO}/venv/bin/pip"
VENV_PY="${REPO}/venv/bin/python3"

if "${VENV_PY}" -c "import luma.oled, PIL, httpx" &>/dev/null; then
    echo "luma.oled + pillow + httpx already installed -- skipping"
else
    echo "Installing luma.oled + pillow + httpx into the venv ..."
    "${VENV_PIP}" install --quiet 'luma.oled>=3.13' 'pillow>=10.0' 'httpx>=0.27'
fi

echo "oled-display setup complete."
