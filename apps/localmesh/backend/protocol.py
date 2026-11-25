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

# commands (app -> receiver) this plugin sends; read-only ones
HELLO, INFO, MEMBERS_GET, CHANNELS_GET, MESSAGES_GET, STATUS, NEIGHBORS_GET = 0x01, 0x02, 0x15, 0x18, 0x21, 0x30, 0x36
RAW = 0x7F        # a text command of the board's line protocol in a frame (its output comes as TEXT events)
TIME_SET = 0x03   # sets the board's clock (the app does this right after HELLO)
NAMES_GET, REPEATERS_GET = 0x19, 0x37   # names the board remembers per member; repeaters from the server's certificates
# and the ones that transmit (the Send tab, admin only)
ADVERT_SET, SEND, SEND_DIRECT, ADVERT_SEND = 0x16, 0x20, 0x22, 0x23
# reply and events (receiver -> app)
REPLY, MESSAGE, RX_LOG, STATUS_EVENT, DELIVERY, ADVERT, DELIVERY_FAILED, TRACE, CHANNEL_EVENT, REPEATER_EVENT, TEXT = (
    0x80, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8F)

ROLES = ["repeater", "companion", "bridge", "observer"]
# packet types over the air (firmware src/Packet.h, packet format v4)
PACKET_TYPES = ["channel", "cert", "block", "admin", "cert_request", "direct", "ack", "advert", "trace", "rekey",
                "rekey_ack", "repeater_cert", "status", "announce", "channel_key", "test"]
# what a board decided about a packet (firmware lm::Decision, same order)
DECISIONS = ["forward", "deliver", "wrong_net", "not_member", "blocked", "replay", "bad_signature", "budget_exceeded",
             "hops_exhausted", "neighbor_rejected", "unknown_type", "member_added", "member_blocked", "not_next_hop",
             "path_full", "duplicate", "looped", "admin_applied", "out_of_region"]
CHANNEL_DIRECT = 255
# reply error codes
ERR_OK, ERR_UNKNOWN, ERR_ARGUMENT, ERR_STATE, ERR_RADIO = 0, 1, 2, 3, 4
# text limits in bytes (firmware Packet.h / Core.h, packet format v4): a channel message carries the receiver's own
# name in front ("Name: "), inside the same 151 bytes; a direct message does not
CHANNEL_TEXT_MAX = 151   # MAX_PAYLOAD 160 - channel byte - 4-byte nonce part - 4-byte MAC (firmware 8e12ea5, 18-11:
                         # the nonce part travels in every channel message; was 155)
DIRECT_TEXT_MAX = 149    # MAX_PAYLOAD 160 - 7-byte direct head - 4-byte MAC
NAME_MAX = 32

MEMBER_BLOCKED = 0x01   # member flags (firmware src/Members.h)
MEMBER_NATIONAL = 0x02
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


def command(type_: int, seq: int, payload: bytes = b"") -> bytes:
    """One SLIP-framed command, ready to write to the port."""
    return slip_encode(frame(type_, seq, payload))


def time_set(seq: int, unix_time: int) -> bytes:
    """The board has no clock of its own after a power-up: the app sets it, and messages carry its time from then on."""
    return command(TIME_SET, seq, struct.pack("<I", unix_time))


def messages_get(seq: int, from_index: int = 0) -> bytes:
    return command(MESSAGES_GET, seq, struct.pack("<H", from_index & 0xFFFF))


def members_get(seq: int, from_index: int = 0) -> bytes:
    return command(MEMBERS_GET, seq, struct.pack("<H", from_index))


def names_get(seq: int, from_index: int = 0) -> bytes:
    return command(NAMES_GET, seq, struct.pack("<H", from_index))


def repeaters_get(seq: int, from_index: int = 0) -> bytes:
    return command(REPEATERS_GET, seq, bytes([from_index & 0xFF]))


def channel_text_max(name: str) -> int:
    """How many bytes of text fit in a channel message from a receiver called ``name``."""
    return CHANNEL_TEXT_MAX - (len(name.encode()) + 2 if name else 0)


def clip_utf8(text: str, max_bytes: int) -> str:
    """Cut to max_bytes without splitting a character (like the firmware's utf8_trim)."""
    b = text.encode()
    if len(b) <= max_bytes:
        return text
    return b[:max_bytes].decode("utf-8", "ignore")


def send_channel(seq: int, channel: int, text: str) -> bytes:
    t = text.encode()
    return command(SEND, seq, bytes([channel, len(t)]) + t)


def send_direct(seq: int, member: int, text: str) -> bytes:
    t = text.encode()
    return command(SEND_DIRECT, seq, struct.pack("<IB", member, len(t)) + t)


def advert_set(seq: int, name: str, lat: int = 0, lon: int = 0, flags: int = 0) -> bytes:
    n = clip_utf8(name, NAME_MAX).encode()
    return command(ADVERT_SET, seq, struct.pack("<Bii", flags, lat, lon) + bytes([len(n)]) + n)


@dataclass
class Sent:
    counter: int; airtime_ms: int


def parse_sent(p: bytes) -> Sent:
    """Reply to SEND / SEND_DIRECT / ADVERT_SEND: the packet's counter and its airtime."""
    r = Reader(p)
    return Sent(r.u32(), r.u16())


def parse_delivery(p: bytes) -> tuple[int, int]:
    """DELIVERY: (by member, counter of our direct message)."""
    r = Reader(p)
    return r.u32(), r.u32()


def parse_delivery_failed(p: bytes) -> tuple[int, int, int]:
    """DELIVERY_FAILED: (member, counter of our direct message, attempts)."""
    r = Reader(p)
    return r.u32(), r.u32(), r.u8()


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
    name: Optional[str] = None   # the board's name on the network (firmware 13-11; None = older firmware, "" = none set)


def parse_info(p: bytes) -> Info:
    r = Reader(p)
    proto, fw, board = r.u8(), r.str(), r.str()
    role = r.u8()
    node, net, member, pub = r.u16(), r.u16(), r.u32(), r.raw(32).hex()
    i = Info(proto, fw, board, ROLES[role] if role < len(ROLES) else str(role), node, net, member, pub,
             r.u8() != 0, round(r.f32(), 3), r.u8(), round(r.f32(), 1), r.u8(), r.i8())
    if r.remaining >= 4:
        r.u32()                      # free heap
        if r.remaining >= 1:
            i.name = r.str()         # 13-11: the board tells its own name
    return i


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


def raw_command(seq: int, line: str) -> bytes:
    """A text command as a frame. Never write it to the port as a plain line: the board then takes the host for a
    terminal and stops sending events until the next HELLO."""
    b = line.encode()[:255]
    return command(RAW, seq, bytes([len(b)]) + b)


def parse_text(p: bytes) -> str:
    return Reader(p).str()


@dataclass
class Announce:
    member: int; counter: int; region: int; version: str


@dataclass
class RepeaterPacket:
    type: str; member: int; counter: int; payload: bytes


def parse_repeater_packet(frame: bytes) -> Optional[RepeaterPacket]:
    """A repeater's own packet as sent over the air (packet format v4, firmware src/Packet.h), or None for anything else.
    The HMAC in the tail needs the network key, so this is what the packet says, not proof of who sent it."""
    if len(frame) < 21 or frame[0] >> 4 != 4 or frame[1] >> 6 != 2:   # 2: the 8-byte HMAC tail these packets have
        return None
    member = int.from_bytes(frame[4:7], "big")
    start = 13 + 2 * (frame[2] >> 4 & 7)                              # 2 bytes per hop in the path
    if member < REPEATER_MEMBER_BASE or len(frame) < start + 8:
        return None
    return RepeaterPacket(PACKET_TYPES[frame[0] & 15], member, int.from_bytes(frame[7:11], "big"), frame[start:-8])


def parse_announce(frame: bytes) -> Optional[Announce]:
    """A repeater's announce; its payload is plain text: version 1, region, firmware (3 bytes)."""
    k = parse_repeater_packet(frame)
    if k is None or k.type != "announce" or len(k.payload) != 5 or k.payload[0] != 1:
        return None
    return Announce(k.member, k.counter, k.payload[1], ".".join(str(b) for b in k.payload[2:5]))


@dataclass
class Message:
    index: int; channel: int; member: int; counter: int; time: int; hops: int; via: int; rssi: int; snr: float; text: str
    path: list = field(default_factory=list)   # v2 (firmware 15-11): the repeaters of a flood, first one first


def parse_message(p: bytes) -> Message:
    r = Reader(p)
    m = Message(r.u16(), r.u8(), r.u32(), r.u32(), r.u32(), r.u8(), r.u16(), r.i16(), r.i8() / 4, r.str())
    if r.remaining >= 1:   # v2: u8 n, n x u16 node (empty for a routed packet or one heard directly)
        n = r.u8()
        m.path = [r.u16() for _ in range(n)]
    return m


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


def parse_channels(p: bytes) -> dict[int, str]:
    """CHANNELS_GET reply: channel id -> name, for every channel the receiver holds a key for."""
    r = Reader(p)
    out = {}
    for _ in range(r.u8()):
        cid, _managed, name = r.u8(), r.u8(), r.str()
        out[cid] = name
    return out


@dataclass
class ChannelEvent:
    id: int; removed: bool; name: str


def parse_channel_event(p: bytes) -> ChannelEvent:
    r = Reader(p)
    return ChannelEvent(r.u8(), r.u8() != 0, r.str())


@dataclass
class MemberEntry:
    id: int; pub: str; flags: int; highest_counter: int

    @property
    def blocked(self) -> bool:
        return bool(self.flags & MEMBER_BLOCKED)


def parse_members(p: bytes) -> tuple[int, list[MemberEntry]]:
    """MEMBERS_GET reply: (total members on the receiver, this page of at most 5)."""
    r = Reader(p)
    total = r.u16()
    return total, [MemberEntry(r.u32(), r.raw(32).hex(), r.u8(), r.u32()) for _ in range(r.u8())]


def parse_names(p: bytes) -> tuple[int, dict[int, str]]:
    """NAMES_GET reply: (total names on the board, this page of member -> last heard name). Firmware 14-11 and later."""
    r = Reader(p)
    total = r.u16()
    out = {}
    for _ in range(r.u8()):
        member = r.u32()
        out[member] = r.str()
    return total, out


@dataclass
class RepeaterEntry:
    node: int; region: int; flags: int; name: str

    @property
    def revoked(self) -> bool:
        return bool(self.flags & 1)


def parse_repeater_entry(r: Reader) -> RepeaterEntry:
    return RepeaterEntry(r.u16(), r.u8(), r.u8(), r.str())


def parse_repeaters(p: bytes) -> tuple[int, list[RepeaterEntry]]:
    """REPEATERS_GET reply: (total repeaters on the board, this page). The names come from the server's certificates
    (firmware 15-11 and later)."""
    r = Reader(p)
    total = r.u8()
    return total, [parse_repeater_entry(r) for _ in range(r.u8())]


def parse_repeater_event(p: bytes) -> RepeaterEntry:
    return parse_repeater_entry(Reader(p))


@dataclass
class Neighbor:
    node: int; snr: float; rssi: int; ago_s: int; count: int


def parse_neighbors(p: bytes) -> list[Neighbor]:
    """NEIGHBORS_GET reply: the repeaters this receiver hears directly."""
    r = Reader(p)
    return [Neighbor(r.u16(), r.i8() / 4, r.i16(), r.u32(), r.u32()) for _ in range(r.u8())]
