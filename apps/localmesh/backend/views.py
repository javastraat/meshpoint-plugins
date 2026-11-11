"""The lists behind the LocalMesh page's Members, Messages and Activity tabs, merged over all receivers.

Pure functions over LocalMeshSerialSource.page_data() copies (one dict per receiver, plus its label), so they are
tested without a board or FastAPI. Newest first everywhere.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from . import protocol as p

Receivers = Iterable[tuple[str, dict[str, Any]]]   # (label, page_data())


def _is_member(member: int) -> bool:
    """A real member: not the server (0) and not a repeater's own status/announce sender."""
    return 0 < member < p.REPEATER_MEMBER_BASE


# the app puts the sender's name in front of a channel message: "Einstein: I am OK"
_NAME_PREFIX = re.compile(r"^([^:\n]{1,32}): ")


def _names(receivers: list) -> dict[int, str]:
    """Member -> name: from an advert, else from the "Name: " in front of their latest message."""
    names: dict[int, str] = {}
    for _label, d in receivers:
        for m in d["messages"]:
            found = _NAME_PREFIX.match(m["text"])
            if found and 0 < m["member"] < p.REPEATER_MEMBER_BASE:
                names[m["member"]] = found.group(1).strip()
    for _label, d in receivers:
        for member, a in d["adverts"].items():
            if a.get("name"):
                names[member] = a["name"]
    return names


def members(receivers: Receivers) -> list[dict]:
    """Every member a receiver knows of: from its member list, from adverts (name, position) and from what it heard
    (count, last heard, hops, signal). With more receivers the freshest observation wins and counts add up."""
    receivers = list(receivers)
    names = _names(receivers)
    out: dict[int, dict] = {}

    def entry(member: int) -> dict:
        return out.setdefault(member, {
            "member": member, "name": "", "lat": None, "lon": None, "blocked": False, "national": False,
            "listed": False, "own": False, "own_receiver": None, "count": 0, "last_heard": None, "hops": None, "rssi": None, "snr": None, "via": None,
            "type": None, "receivers": [],
        })

    for label, d in receivers:
        for member, m in d["members"].items():
            e = entry(member)
            e["listed"] = True
            e["blocked"] = e["blocked"] or bool(m.get("blocked"))
            e["national"] = e["national"] or bool(m.get("flags", 0) & p.MEMBER_NATIONAL)
        for member, a in d["adverts"].items():
            if not _is_member(member):
                continue
            e = entry(member)
            if a.get("name"):
                e["name"] = a["name"]
            if a.get("lat") is not None:
                e["lat"], e["lon"] = a["lat"], a["lon"]
            if e["last_heard"] is None or a["heard"] > e["last_heard"]:
                e.update(last_heard=a["heard"], hops=a["hops"], rssi=a["rssi"], via=a["via"], type="advert")
        for member, h in d["heard"].items():
            if not _is_member(member):
                continue
            e = entry(member)
            e["count"] += h["count"]
            if label not in e["receivers"]:
                e["receivers"].append(label)
            if e["last_heard"] is None or h["last_heard"] >= e["last_heard"]:
                e.update(last_heard=h["last_heard"], hops=h["hops"], rssi=h["rssi"], snr=h["snr"], via=h["via"],
                         type=h["type"])
    for label, d in receivers:   # the receiver itself is a member too: "this Meshpoint", with its own name
        own = d.get("own_member")
        if own:
            e = entry(own)
            e.update(own=True, own_receiver=label)
            e["name"] = e["name"] or d.get("net_name") or ""
    for e in out.values():
        e["name"] = e["name"] or names.get(e["member"], "")
    return sorted(out.values(), key=lambda e: (-(e["last_heard"] or 0), e["member"]))


def messages(receivers: Receivers, limit: int = 200) -> list[dict]:
    """Channel messages and direct messages to a receiver, once each (two receivers can both get a channel message),
    and what the Send tab sent (``outgoing``, with its ``status``: sent, repeated, delivered or failed)."""
    receivers = list(receivers)
    names = _names(receivers)
    seen: set = set()
    out: list[dict] = []
    for label, d in receivers:
        for m in d["messages"]:
            key = (m["member"], m["counter"], m["channel"])
            if key in seen:
                continue
            seen.add(key)
            direct = m["channel"] == p.CHANNEL_DIRECT
            sender = names.get(m["member"], "")
            text = m["text"]
            prefix = _NAME_PREFIX.match(text)
            if not direct and prefix and sender and prefix.group(1).strip() == sender:
                text = text[prefix.end():]   # "Einstein: hoi" -> "hoi": the name is already under From
            out.append({
                **m, "text": text, "receiver": label, "direct": direct, "sender": sender,
                "channel_name": f"direct to {label or 'receiver'}" if direct
                else d["channels"].get(m["channel"]) or f"channel {m['channel']}",
                "sort_time": m["time"] or m["received"],
            })
        for x in d.get("sent", []):   # what the Send tab sent through this receiver
            to = x["to"]
            out.append({
                "index": None, "channel": x["channel"], "member": d.get("own_member"), "counter": x["counter"],
                "time": x["time"], "received": x["time"], "hops": None, "via": None, "rssi": None, "snr": None,
                "text": x["text"], "receiver": label, "direct": x["direct"], "outgoing": True, "status": x["status"],
                "airtime_ms": x["airtime_ms"], "to": to, "sender": d.get("net_name") or f"Meshpoint ({label or 'receiver'})",
                "channel_name": (f"direct to {names.get(to) or to}" if x["direct"]
                                 else d["channels"].get(x["channel"]) or f"channel {x['channel']}"),
                "sort_time": x["time"],
            })
    out.sort(key=lambda m: -m["sort_time"])
    return out[:limit]


def activity(receivers: Receivers, limit: int = 200) -> list[dict]:
    """The packets the receivers heard, with their decision; one row per receiver that heard it."""
    receivers = list(receivers)
    names = _names(receivers)
    out = [{**a, "receiver": label, "name": names.get(a["member"], "")}
           for label, d in receivers for a in d["activity"]]
    out.sort(key=lambda a: -a["time"])
    return out[:limit]
