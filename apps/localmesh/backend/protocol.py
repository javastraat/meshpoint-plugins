"""LocalMesh companion protocol v1 over USB: SLIP framing and the frames this plugin reads.

Pure stdlib, so the tests run on any machine. Mirrors the LocalMesh firmware (src/Protocol.h) and the LocalMesh
Connect app (lib/protocol/protocol.dart); the English summary of every code is docs/protocol-en.md in the firmware
repo. Little-endian; ``str`` = u8 length + UTF-8. A frame is ``[type u8][seq u8][payload]``: commands carry a
sequence number 1..255 and get a REPLY with the same number; events have sequence 0.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

PROTOCOL_VERSION = 1

# commands (app -> receiver) this plugin sends
HELLO, INFO, STATUS = 0x01, 0x02, 0x30
# reply and events (receiver -> app)
REPLY, MESSAGE, RX_LOG, STATUS_EVENT, DELIVERY, ADVERT, DELIVERY_FAILED, TRACE, CHANNEL_EVENT, TEXT = (
    0x80, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x8F)

ROLES = ["repeater", "companion", "bridge", "observer"]
# packet types over the air (firmware src/Packet.h, packet format v4)
PACKET_TYPES = ["channel", "cert", "block", "admin", "measure", "direct", "ack", "advert", "trace", "rekey",
                "rekey_ack", "repeater_cert", "status", "announce", "channel_key", "test"]
# what a board decided about a packet (firmware lm::Decision, same order)
DECISIONS = ["forward", "deliver", "wrong_net", "not_member", "blocked", "replay", "bad_signature", "budget_exceeded",
             "hops_exhausted", "neighbor_rejected", "unknown_type", "member_added", "member_blocked", "not_next_hop",
             "path_full", "duplicate", "looped", "admin_applied", "out_of_region"]
CHANNEL_DIRECT = 255
REPEATER_MEMBER_BASE = 0xFF0000   # member ids 0xFF0000 | node are a repeater's own status/announce packets

SLIP_END, SLIP_ESC, SLIP_ESC_END, SLIP_ESC_ESC = 0xC0, 0xDB, 0xDC, 0xDD
MAX_FRAME = 300


def slip_encode(frame: bytes) -> bytes:
    out = bytearray([SLIP_END])
    for b in frame:
        if b == SLIP_END:
            out += bytes([SLIP_ESC, SLIP_ESC_END])
        elif b == SLIP_ESC:
            out += bytes([SLIP_ESC, SLIP_ESC_ESC])
        else:
            out.append(b)
    out.append(SLIP_END)
    return bytes(out)


class SlipDecoder:
    """Feed bytes as they arrive; get complete frames back, plus text lines outside frames (the receiver's line
    protocol and log lines, which share the USB port). Same rules as the app's decoder: an empty C0 C0 is still
    between frames, and a frame longer than MAX_FRAME is dropped."""

    def __init__(self) -> None:
        self._frame = bytearray(); self._text = bytearray()
        self._in_frame = self._esc = self._too_long = False

    def feed(self, data: bytes) -> tuple[list[bytes], list[str]]:
        frames: list[bytes] = []; lines: list[str] = []
        for b in data:
            if self._in_frame:
                if self._esc:
                    self._add(SLIP_END if b == SLIP_ESC_END else SLIP_ESC if b == SLIP_ESC_ESC else b); self._esc = False
                elif b == SLIP_ESC:
                    self._esc = True
                elif b == SLIP_END:
                    if not self._frame:
                        self._too_long = False; continue
                    if not self._too_long:
                        frames.append(bytes(self._frame))
                    self._frame.clear(); self._too_long = False; self._in_frame = False
                else:
                    self._add(b)
            elif b == SLIP_END:
                self._in_frame = True; self._frame.clear(); self._esc = False
            elif b in (10, 13):
                if self._text:
                    lines.append(self._text.decode("utf-8", "replace")); self._text.clear()
            else:
                self._text.append(b)
        return frames, lines

    def _add(self, b: int) -> None:
        if len(self._frame) < MAX_FRAME:
            self._frame.append(b)
        else:
            self._too_long = True


def frame(type_: int, seq: int, payload: bytes = b"") -> bytes:
    return bytes([type_, seq]) + payload


def hello(seq: int, app_name: str = "Meshpoint LocalMesh plugin") -> bytes:
    name = app_name.encode()[:255]
    return slip_encode(frame(HELLO, seq, bytes([PROTOCOL_VERSION, len(name)]) + name))


class Reader:
    """Little-endian reader; running past the end raises ValueError (a short or foreign frame)."""

    def __init__(self, data: bytes) -> None:
        self.d = data; self.i = 0

    @property
    def remaining(self) -> int:
        return len(self.d) - self.i

    def _take(self, n: int) -> bytes:
        if self.i + n > len(self.d):
            raise ValueError("frame too short")
        b = self.d[self.i:self.i + n]; self.i += n; return b

    def u8(self) -> int: return self._take(1)[0]
    def i8(self) -> int: return struct.unpack("<b", self._take(1))[0]
    def u16(self) -> int: return struct.unpack("<H", self._take(2))[0]
    def i16(self) -> int: return struct.unpack("<h", self._take(2))[0]
    def u32(self) -> int: return struct.unpack("<I", self._take(4))[0]
    def i32(self) -> int: return struct.unpack("<i", self._take(4))[0]
    def f32(self) -> float: return struct.unpack("<f", self._take(4))[0]
    def str(self) -> str: return self._take(self.u8()).decode("utf-8", "replace")
    def raw(self, n: int) -> bytes: return self._take(n)


@dataclass
class Info:
    protocol: int; firmware: str; board: str; role: str; node: int; net_id: int; member: int; pub: str
    serverpub_set: bool; freq_mhz: float; sf: int; bw_khz: float; cr: int; power_dbm: int


def parse_info(p: bytes) -> Info:
    r = Reader(p)
    proto, fw, board = r.u8(), r.str(), r.str()
    role = r.u8()
    node, net, member, pub = r.u16(), r.u16(), r.u32(), r.raw(32).hex()
    return Info(proto, fw, board, ROLES[role] if role < len(ROLES) else str(role), node, net, member, pub,
                r.u8() != 0, round(r.f32(), 3), r.u8(), round(r.f32(), 1), r.u8(), r.i8())


@dataclass
class Status:
    battery: Optional[int]; budget_ms: int; budget_limit: int; rx_ok: int; rx_rejected: int; tx: int
    rssi: int; snr: float; noise_floor: Optional[int] = None; battery_mv: Optional[int] = None
    uptime_s: Optional[int] = None; duty: Optional[int] = None


def parse_status(p: bytes) -> Status:
    r = Reader(p)
    battery = r.u8()
    s = Status(None if battery == 255 else battery, r.u32(), r.u32(), r.u32(), r.u32(), r.u32(), r.i16(), r.i8() / 4)
    if r.remaining >= 22:   # extended (firmware 0.3.0+; radio restarts after it are not used)
        s.noise_floor = r.i16(); r.u32(); r.u32(); r.u32(); s.uptime_s = r.u32(); mv = r.u16()
        s.battery_mv = mv or None; r.u8(); s.duty = r.u8()
    return s


@dataclass
class RxLog:
    member: int; counter: int; type: str; hops: int; via: int; rssi: int; snr: float; decision: str
    routed: bool = False; path: list = field(default_factory=list)   # [(node, snr), ...]


def parse_rx_log(p: bytes) -> RxLog:
    r = Reader(p)
    member, counter, t, hops, via, rssi, snr, dec = r.u32(), r.u32(), r.u8(), r.u8(), r.u16(), r.i16(), r.i8() / 4, r.u8()
    log = RxLog(member, counter, PACKET_TYPES[t] if t < len(PACKET_TYPES) else f"type{t}", hops, via, rssi, snr,
                DECISIONS[dec] if dec < len(DECISIONS) else f"decision{dec}")
    if r.remaining >= 2:   # v2: routed + path
        log.routed = r.u8() != 0
        log.path = [(r.u16(), r.i8() / 4) for _ in range(r.u8())]
    return log


@dataclass
class Message:
    index: int; channel: int; member: int; counter: int; time: int; hops: int; via: int; rssi: int; snr: float; text: str


def parse_message(p: bytes) -> Message:
    r = Reader(p)
    return Message(r.u16(), r.u8(), r.u32(), r.u32(), r.u32(), r.u8(), r.u16(), r.i16(), r.i8() / 4, r.str())


@dataclass
class Advert:
    member: int; flags: int; lat: Optional[float]; lon: Optional[float]; name: str; hops: int; via: int; rssi: int


def parse_advert(p: bytes) -> Advert:
    r = Reader(p)
    member, flags, lat, lon, name = r.u32(), r.u8(), r.i32(), r.i32(), r.str()
    known = not (lat == 0 and lon == 0)
    return Advert(member, flags, lat / 1e7 if known else None, lon / 1e7 if known else None, name, r.u8(), r.u16(), r.i16())


def member_label(member: int) -> str:
    """How a sender shows: the server, a repeater (its own status/announce packets), or a member number."""
    if member == 0:
        return "server"
    if member >= REPEATER_MEMBER_BASE:
        return f"repeater {member - REPEATER_MEMBER_BASE}"
    return str(member)
