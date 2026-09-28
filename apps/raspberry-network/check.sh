#!/usr/bin/env bash
#
# raspberry-network plugin dependency check.
#
# Unprivileged probe run by meshpoint at boot (and on demand from
# Settings -> Plugins). Not every supported board runs NetworkManager --
# the Bobcat Miner 300 runs community Armbian, which may not -- so this
# has to actually verify the real backend is present and active, not
# just assume it because the target OS usually ships it.
#
#   exit 0        -- nmcli present and NetworkManager active, nothing to do
#   exit non-zero -- can't be used on this board; stdout says why
set -uo pipefail

if ! command -v nmcli &>/dev/null; then
    echo "nmcli not found -- this board isn't running NetworkManager"
    echo "(no setup step exists for this -- NetworkManager isn't something"
    echo "this plugin can install; it's a base-OS networking stack choice)"
    exit 1
fi

if ! nmcli -t -f RUNNING general status 2>/dev/null | grep -q "^running$"; then
    echo "nmcli is installed but NetworkManager isn't running"
    echo "check: sudo systemctl status NetworkManager"
    exit 1
fi

echo "nmcli present, NetworkManager running"
exit 0
