#!/usr/bin/env bash
#
# offline-map plugin dependency check.
#
# Unprivileged probe run by meshpoint at boot (and on demand from
# Settings -> Plugins). Mirrors setup.sh's own idempotency check.
#
#   exit 0        -- binary built, nothing to do
#   exit non-zero -- setup needed; stdout says what's missing
#
# No sudo: a file existence/executable check needs no privileges.
set -uo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BINARY="$PLUGIN_DIR/bin/offline-map-tile-downloader"

if [ -x "$BINARY" ]; then
    echo "offline-map-tile-downloader built ($BINARY)"
    exit 0
fi

echo "offline-map-tile-downloader is not built yet (built from source by setup.sh)"
echo "run: sudo meshpoint plugin setup offline-map"
exit 1
