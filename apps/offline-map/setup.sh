#!/usr/bin/env bash
#
# offline-map plugin dependency: builds Cyclenerd/offline-map-tile-downloader
# (vendored at offline-map-src/) into this plugin's own bin/ -- not a
# system-wide install like ACARS's acarsdec, so there's nothing outside
# this plugin's own folder to clean up if it's ever deleted.
#
# The only step that needs root is `apt install golang-go`; the build
# itself doesn't. This whole script still runs as root (the standard
# `sudo bash .../setup.sh` path every plugin's setup uses), so the last
# step hands bin/ back to the meshpoint user -- otherwise the built
# binary would sit root-owned inside an otherwise meshpoint-owned tree.
#
# Idempotent: skips the build if the binary already exists.
#
# Run once, admin-triggered from Settings -> Plugins "Run setup", or:
#     sudo bash plugins/apps/offline-map/setup.sh
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$PLUGIN_DIR/offline-map-src"
BIN_DIR="$PLUGIN_DIR/bin"
BINARY="$BIN_DIR/offline-map-tile-downloader"

if [ -x "$BINARY" ]; then
    echo "$BINARY already built -- nothing to do"
    exit 0
fi

if ! command -v go &>/dev/null; then
    echo "==> Installing Go toolchain (golang-go) ..."
    apt-get install -y -qq --no-install-recommends golang-go
fi

echo "==> Building offline-map-tile-downloader from $SRC_DIR ..."
mkdir -p "$BIN_DIR"
(
    cd "$SRC_DIR"
    go build -o "$BINARY" .
)

echo "==> Handing $BIN_DIR back to meshpoint:meshpoint ..."
chown -R meshpoint:meshpoint "$BIN_DIR"

echo "Built $BINARY"
