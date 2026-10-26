#!/usr/bin/env python3
"""Fetch a NomadNet path (a `.mu` page or a JSON `/api/*` path) from a
Reticulum destination through your own meshpoint instance -- no RNS/
reticulum dependency of its own, just plain HTTP against meshpoint.

Reuses the exact same code path the Browse tab / Reticulum Browser /
Reticulum Dashboard quick-browse modal already use
(backend/nomad.py's fetch_page()), via POST /api/reticulum/nomad/page.
Needs a logged-in meshpoint account (viewer is enough -- that route only
requires require_auth).

Credentials via --user/--password, or MESHPOINT_USER/MESHPOINT_PASS env
vars if you'd rather not have the password sitting in shell history /
`ps` -- flags win when both are given.

Usage:
    python3 nomad_fetch.py --user you --password '...' <destination_hash> [path]

Example (a ScotMesh repeater's live telemetry):
    python3 nomad_fetch.py --url http://192.168.1.50:8080 --user you --password '...' \\
        deadbeef00112233445566778899aabb /api/all
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "http://localhost:8080"
DEFAULT_PATH = "/page/index.mu"


def _post(url: str, payload: dict, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        headers["X-Meshpoint-Client"] = "app"  # ask /login for a bearer token in the body
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        sys.exit(f"HTTP {exc.code} from {url}: {body}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("destination_hash", help="Reticulum destination hash (hex)")
    parser.add_argument("path", nargs="?", default=DEFAULT_PATH, help=f"NomadNet path (default: {DEFAULT_PATH})")
    parser.add_argument("--url", default=os.environ.get("MESHPOINT_URL", DEFAULT_URL), help="meshpoint base URL")
    parser.add_argument("--user", default=os.environ.get("MESHPOINT_USER"), help="meshpoint username")
    parser.add_argument("--password", default=os.environ.get("MESHPOINT_PASS"), help="meshpoint password")
    args = parser.parse_args()

    user = args.user
    password = args.password
    if not user or not password:
        sys.exit("need credentials: --user/--password, or MESHPOINT_USER/MESHPOINT_PASS env vars")

    base = args.url.rstrip("/")
    login = _post(f"{base}/api/auth/login", {"username": user, "password": password})
    token = login.get("token")
    if not token:
        sys.exit(f"login did not return a token: {login}")

    result = _post(
        f"{base}/api/reticulum/nomad/page",
        {"destination_hash": args.destination_hash, "path": args.path},
        token=token,
    )
    if not result.get("ok"):
        sys.exit(f"fetch failed: {result.get('error')}")

    content = result["content"]
    # /api/* paths return JSON text; .mu pages return Micron markup --
    # pretty-print if it parses as JSON, else print as-is.
    try:
        print(json.dumps(json.loads(content), indent=2))
    except (json.JSONDecodeError, TypeError):
        print(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
