#!/usr/bin/env python3
"""Derive the LocalMesh status key from the network key, for the LocalMesh plugin's Settings tab.

Run this on your own computer, NOT on the Meshpoint: only the status key has to go there. It opens the repeaters' status
packets; nobody can get the network key back from it or send anything with it. After a network key change: run it again.

  python3 status_key.py        asks for the network key (64 hex, not shown while typing)
"""
import getpass
import hashlib
import hmac
import re
import sys


def derive(net_key: bytes) -> str:
    return "lmst1-" + hmac.new(net_key, b"status", hashlib.sha256).hexdigest()


def fingerprint(net_key: bytes) -> str:   # firmware net_key_check: what NETKEY on a repeater shows
    return hmac.new(net_key, b"check", hashlib.sha256).hexdigest()[:8]


def main() -> int:
    if len(sys.argv) > 1:
        print(__doc__)
        if sys.argv[1] not in ("-h", "--help"):
            print("The key is not taken from the command line: it would stay in the shell history.", file=sys.stderr)
            return 2
        return 0
    try:
        text = getpass.getpass("Network key (64 hex): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return 130
    if not re.fullmatch(r"[0-9a-fA-F]{64}", text):
        print("That is not 64 hex characters.", file=sys.stderr)
        return 1
    key = bytes.fromhex(text)
    print("Network key fingerprint:", fingerprint(key), "(compare with NETKEY on a repeater)")
    print("Status key:             ", derive(key))
    return 0


if __name__ == "__main__":
    sys.exit(main())
