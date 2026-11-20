"""The bot's decisions, without Meshpoint, FastAPI or a receiver: pure Python, tested in tests/test_bot.py.

A message comes in as the dict the localmesh plugin keeps (listener.py _note_message: member, channel, counter, time,
hops, via, rssi, snr, text, path). decide() returns the reply text, or None, and why:

- only on the configured channels, never a direct message (channels only, for now)
- never our own messages, nor a message with our own name in front (the firmware puts the receiver's name in front of
  every channel message it sends; another receiver with the same name would otherwise start a loop)
- never an old message: the receiver hands its whole buffer over again after a restart
- only text that starts with the prefix (".ping"); an unknown command gets no answer (airtime)
- at most one answer per member per cooldown, and at most max_per_hour answers in all: the band allows 10 % airtime and
  every answer is a flood through every repeater
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

CHANNEL_DIRECT = 255
BUILTIN = ("help", "ping", "trace")
COMMAND_RE = re.compile(r"^[a-z0-9_-]{1,16}$")
ANSWER_MAX = 140   # characters of a custom answer
_NAME_PREFIX = re.compile(r"^([^:\n]{1,32}): ")   # same as the localmesh plugin's views.py


@dataclass
class Config:
    enabled: bool = False
    receiver: str = ""                                 # the receiver's label ("" = the first one)
    channels: list = field(default_factory=list)       # channel ids it listens on
    prefix: str = "."
    commands: dict = field(default_factory=dict)       # own commands: name -> answer
    cooldown_s: int = 20                                # per member
    max_per_hour: int = 30                              # in all
    max_age_s: int = 120                                # older messages (buffer after a restart) get no answer

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        c = cls()
        c.enabled = bool(d.get("enabled", False))
        c.receiver = str(d.get("receiver") or "")
        c.channels = sorted({int(x) for x in (d.get("channels") or []) if 0 <= int(x) < CHANNEL_DIRECT})
        prefix = str(d.get("prefix") or ".")
        c.prefix = prefix if len(prefix) == 1 and not prefix.isalnum() and not prefix.isspace() else "."
        c.commands = {str(k).lower(): str(v) for k, v in (d.get("commands") or {}).items()
                      if COMMAND_RE.match(str(k).lower()) and str(k).lower() not in BUILTIN and str(v).strip()}
        c.cooldown_s = max(5, min(int(d.get("cooldown_s", 20)), 3600))
        c.max_per_hour = max(1, min(int(d.get("max_per_hour", 30)), 120))
        c.max_age_s = max(30, min(int(d.get("max_age_s", 120)), 3600))
        return c

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "receiver": self.receiver, "channels": list(self.channels),
                "prefix": self.prefix, "commands": dict(self.commands), "cooldown_s": self.cooldown_s,
                "max_per_hour": self.max_per_hour, "max_age_s": self.max_age_s}


def validate(d: dict) -> list[str]:
    """What is wrong with a config from the Bot tab (empty = fine). from_dict() drops bad entries quietly; the tab
    gets told instead."""
    errors = []
    for x in d.get("channels") or []:
        if not isinstance(x, int) or not 0 <= x < CHANNEL_DIRECT:
            errors.append(f"channel {x!r}: a number 0-254")
    prefix = d.get("prefix", ".")
    if not isinstance(prefix, str) or len(prefix) != 1 or prefix.isalnum() or prefix.isspace():
        errors.append("prefix: one character such as . or !")
    for k, v in (d.get("commands") or {}).items():
        name = str(k).lower()
        if not COMMAND_RE.match(name):
            errors.append(f"command {k!r}: 1-16 of a-z, 0-9, _ and -")
        elif name in BUILTIN:
            errors.append(f"command {k!r}: built in already")
        if not str(v).strip():
            errors.append(f"command {k!r}: the answer is empty")
        elif len(str(v)) > ANSWER_MAX:
            errors.append(f"command {k!r}: the answer is longer than {ANSWER_MAX} characters")
    return errors


def split_name(text: str) -> tuple[str, str]:
    """("Einstein Tag", ".ping") for "Einstein Tag: .ping"; ("", text) without a name in front."""
    m = _NAME_PREFIX.match(text)
    return (m.group(1).strip(), text[m.end():]) if m else ("", text)


def clip_utf8(text: str, max_bytes: int) -> str:
    b = text.encode()
    if len(b) <= max_bytes:
        return text
    return b[:max_bytes].decode(errors="ignore")


def signal(msg: dict) -> str:
    return f"{msg.get('rssi', 0)} dBm / {msg.get('snr', 0):g} dB"


def _hops(path: list) -> str:
    return "1 hop" if len(path) == 1 else f"{len(path)} hops"


# The signal (rssi/snr) is how the bot's receiver heard the LAST transmitter: the asker only when it was heard directly,
# otherwise the last repeater -- the same for everyone behind that repeater (20-11). The answers say which it is.
# "via" is that last transmitter too, but for a message heard directly it is the sender's own node, so only the path
# (MESSAGE v2, firmware 15-11) says which repeaters it went through.

def ping_text(msg: dict, repeater_name: Callable[[int], str]) -> str:
    path = list(msg.get("path") or [])
    if not path:
        return f"pong, heard directly, {signal(msg)}"
    return f"pong, {_hops(path)}, last hop {repeater_name(path[-1])} {signal(msg)}"


def route(msg: dict, repeater_name: Callable[[int], str]) -> str:
    """How the message came: the repeaters of the flood in order and the last hop's signal, or "heard directly"."""
    path = list(msg.get("path") or [])
    if not path:
        return f"heard directly, no repeater, {signal(msg)}"
    return f"{_hops(path)}: " + " → ".join(repeater_name(n) for n in path) + f", last hop {signal(msg)}"


@dataclass
class Context:
    own_member: int
    own_name: str
    now: float
    text_max: int                                   # bytes the receiver can send (its name in front taken off)
    repeater_name: Callable[[int], str] = lambda n: str(n)


class Limiter:
    """One answer per member per cooldown, at most max_per_hour in all (sliding hour)."""

    def __init__(self) -> None:
        self._last: dict[int, float] = {}
        self._hour: collections.deque = collections.deque()

    def allow(self, member: int, now: float, cfg: Config) -> Optional[str]:
        while self._hour and now - self._hour[0] >= 3600:
            self._hour.popleft()
        if now - self._last.get(member, -1e18) < cfg.cooldown_s:
            return "cooldown"
        if len(self._hour) >= cfg.max_per_hour:
            return "hourly limit"
        return None

    def note(self, member: int, now: float) -> None:
        self._last[member] = now
        self._hour.append(now)


def answer(command: str, msg: dict, cfg: Config, ctx: Context) -> Optional[str]:
    if command == "help":
        names = list(BUILTIN) + sorted(cfg.commands)
        return "Commands: " + " ".join(cfg.prefix + n for n in names)
    if command == "ping":
        return ping_text(msg, ctx.repeater_name)
    if command == "trace":
        return route(msg, ctx.repeater_name)
    return cfg.commands.get(command)


def decide(msg: dict, cfg: Config, ctx: Context, limiter: Limiter) -> tuple[Optional[str], str]:
    """(reply, why). reply None = stay quiet; why says what happened (for the Bot tab's log)."""
    if not cfg.enabled:
        return None, "off"
    channel = msg.get("channel")
    if channel == CHANNEL_DIRECT or channel not in cfg.channels:
        return None, "not a bot channel"
    if msg.get("member") == ctx.own_member:
        return None, "own message"
    t = msg.get("time") or msg.get("received")
    if t and ctx.now - t > cfg.max_age_s:
        return None, "too old"
    sender, body = split_name(msg.get("text") or "")
    if ctx.own_name and sender == ctx.own_name:
        return None, "our own name in front"
    body = body.strip()
    if not body.startswith(cfg.prefix) or len(body) < 2:
        return None, "not a command"
    command = body[1:].split()[0].lower()
    reply = answer(command, msg, cfg, ctx)
    if reply is None:
        return None, f"unknown command {command}"
    blocked = limiter.allow(msg.get("member", 0), ctx.now, cfg)
    if blocked:
        return None, blocked
    limiter.note(msg.get("member", 0), ctx.now)
    if sender:
        reply = f"@{sender} {reply}"
    return clip_utf8(reply, ctx.text_max), f"{cfg.prefix}{command}"
