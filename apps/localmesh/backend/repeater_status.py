"""Open and read a repeater's sealed Status packet (firmware Core.cpp make_status / seal_status / read_status).

The contents are encrypted with a *status key*: HMAC-SHA256(network key, "status"). That is one-way, so this plugin never
needs the network key (tools/status_key.py derives the status key elsewhere). It opens a status but proves nothing: the
HMAC tail needs a key from the network key itself. So what comes out is checked for plausibility and shown as unverified.

A status also names members (over budget, biggest senders, blocked ones still sending). Those are skipped here on purpose:
they are never kept, logged or shown. Pure stdlib.
"""

from __future__ import annotations

import re
import struct
from typing import Iterable, Optional

KEY_PREFIX = "lmst1-"   # so a network key (64 hex, no prefix) pasted by mistake is refused
_KEY = re.compile(r"^lmst1-([0-9a-fA-F]{64})$")

MAX_NEIGHBORS, MAX_OVERRUNS, MAX_TOP, MAX_REASONS, MAX_BLOCKED = 10, 5, 3, 32, 3
REASONS = ("bad_signature", "replay", "blocked", "not_member", "wrong_net", "unknown_type", "budget_exceeded",
           "neighbor_rejected")   # firmware STATUS_REASON_DECISIONS, same order


def parse_key(text: str) -> Optional[bytes]:
    """The 32 bytes of a status key as status_key.py prints it, or None."""
    found = _KEY.match(text.strip())
    return bytes.fromhex(found.group(1)) if found else None


def format_key(key: bytes) -> str:
    return KEY_PREFIX + key.hex()


def chacha20(key: bytes, nonce: bytes, data: bytes) -> bytes:
    """RFC 8439 with the block counter starting at 0, as the firmware does. A status is at most three blocks."""
    m = 0xFFFFFFFF
    out = bytearray()
    for block in range((len(data) + 63) // 64):
        init = [0x61707865, 0x3320646E, 0x79622D32, 0x6B206574, *struct.unpack("<8I", key), block,
                *struct.unpack("<3I", nonce)]
        x = list(init)

        def quarter(a: int, b: int, c: int, d: int) -> None:
            for p, q, r, rot in ((a, b, d, 16), (c, d, b, 12), (a, b, d, 8), (c, d, b, 7)):
                x[p] = (x[p] + x[q]) & m
                v = x[r] ^ x[p]
                x[r] = (v << rot | v >> (32 - rot)) & m

        for _ in range(10):
            quarter(0, 4, 8, 12); quarter(1, 5, 9, 13); quarter(2, 6, 10, 14); quarter(3, 7, 11, 15)
            quarter(0, 5, 10, 15); quarter(1, 6, 11, 12); quarter(2, 7, 8, 13); quarter(3, 4, 9, 14)
        stream = struct.pack("<16I", *((a + b) & m for a, b in zip(x, init)))
        out += bytes(a ^ b for a, b in zip(data[64 * block:64 * block + 64], stream))
    return bytes(out)


def unseal(key: bytes, member: int, counter: int, payload: bytes) -> bytes:
    """The plain payload: everything after the version byte is sealed, nonce (0x57, member, counter, 0)."""
    nonce = bytes([0x57]) + member.to_bytes(3, "big") + counter.to_bytes(4, "big") + bytes(4)
    return payload[:1] + chacha20(key, nonce, payload[1:])


def read(q: bytes) -> Optional[dict]:
    """A plain status payload (version 1, 2 or 3), or None when it is not a plausible one (wrong key, damaged)."""
    if len(q) < 16 or q[0] not in (1, 2, 3) or (q[0] == 1 and len(q) != 16):
        return None
    battery, noise, uptime, airtime, queue, ok, rejected = struct.unpack(">HbHBBHH", q[1:12])
    if battery > 6000 or noise > 0 or airtime > 100 or q[12] > 9 or q[13] > 99:
        return None
    neighbors: list[dict] = []
    reasons: dict[str, int] = {}
    if q[0] >= 2:
        if len(q) < 17 or q[16] > MAX_NEIGHBORS or len(q) < 17 + 3 * q[16]:
            return None
        i = 17 + 3 * q[16]
        neighbors = [{"node": n, "snr": s / 4} for n, s in struct.iter_unpack(">Hb", q[17:i])]
        if q[0] == 3:   # members over budget (4 bytes each), biggest senders (5), refusal reasons (1), blocked members (4)
            for size, most in ((4, MAX_OVERRUNS), (5, MAX_TOP), (1, MAX_REASONS), (4, MAX_BLOCKED)):
                if i >= len(q) or q[i] > most or len(q) < i + 1 + size * q[i]:
                    return None
                end = i + 1 + size * q[i]
                if size == 1:
                    reasons = {k: v for k, v in zip(REASONS, q[i + 1:end]) if v}
                i = end   # the entries that name members are skipped, see the module text
        if i != len(q):
            return None
    return {"battery_mv": battery, "noise_floor": noise, "uptime_h": uptime, "airtime_pct": airtime, "queue": queue,
            "rx_ok": ok, "rx_rejected": rejected, "version": ".".join(str(b) for b in q[12:15]),
            "neighbor_count": q[15], "neighbors": neighbors, "reasons": reasons}


def open_status(keys: Iterable[bytes], member: int, counter: int, payload: bytes) -> Optional[dict]:
    """The first key that gives something plausible (the current one, then the one before a key change)."""
    for key in keys:
        out = read(unseal(key, member, counter, payload)) if payload else None
        if out is not None:
            return out
    return None
