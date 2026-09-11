#!/bin/bash
set -e

BINARY=/usr/local/bin/offline-map-tile-downloader
MAPS_DIR=/var/lib/offline-map/maps
LOG_FILE=/var/log/offline-map.log
SERVICE_FILE=/etc/systemd/system/offline-map.service

# Must run as root
if [ "$(id -u)" -ne 0 ]; then
    echo "Please run as root: sudo ./install.sh"
    exit 1
fi

echo "==> Stopping existing service if running..."
service offline-map stop 2>/dev/null || true

echo "==> Removing existing binary if exists..."
rm offline-map-tile-downloader 2>/dev/null || true

echo "==> Building binary..."
go build -o offline-map-tile-downloader .

echo "==> Installing binary to $BINARY..."
cp offline-map-tile-downloader "$BINARY"
chmod +x "$BINARY"

echo "==> Creating maps directory at $MAPS_DIR..."
mkdir -p "$MAPS_DIR"
chown nobody:nogroup "$MAPS_DIR"

echo "==> Creating log file at $LOG_FILE..."
touch "$LOG_FILE"
chown nobody:nogroup "$LOG_FILE"

echo "==> Installing systemd service..."
cp offline-map.service "$SERVICE_FILE"
systemctl daemon-reload

echo "==> Starting service..."
service offline-map start

echo ""
echo "Done! You can now manage the service with:"
echo "  systemctl start offline-map      # start now"
echo "  systemctl enable offline-map     # auto-start on boot"
echo "  systemctl stop offline-map       # stop"
echo "  systemctl status offline-map     # check status"
echo "  journalctl -u offline-map -f     # follow systemd logs"
echo "  tail -f $LOG_FILE                # follow app logs"
echo ""
echo "The web interface will be available at http://localhost:8080"
