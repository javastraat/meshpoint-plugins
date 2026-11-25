"""Fills a receiver's lists after a restart of Meshpoint with what its database still has:

* ``packets``     every packet heard (header, signal, decision): Activity, last heard, counts, announces
* ``messages``    channel and direct messages, received and sent
* ``lmr_latest``  the localmesh-repeaters plugin's last status per repeater, when that plugin is there

Runs once per source, from wire(): the source already runs by then (the core starts it first), so only rows from before
its start are read and the result goes behind what came in live. Every part is best effort: a table that is
missing or a row that cannot be read is skipped. ``db`` is anything with an async fetch_all(sql, params).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from . import protocol as p

logger = logging.getLogger(__name__)

ACTIVITY, MESSAGES, SENT = 300, 300, 100
OLD_TYPES = {"measure": "cert_request"}   # stored under its old name until 25-11


def unix(text: Any) -> Optional[float]:
    try:
        t = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp()


def packet_entry(row: dict) -> Optional[dict]:
    """An Activity entry (as listener._note_rx makes them) from a row of ``packets``."""
    t = unix(row.get("timestamp"))
    try:
        d = json.loads(row.get("decoded_payload") or "")
        member, counter, kind = int(d["member"]), int(d["counter"]), str(d["type"])
        path = [{"node": int(h["node"]), "snr": h["snr"]} for h in d.get("path") or []]
        hops, via, decision = int(d["hops"]), int(d["via"]), str(d["decision"])
    except (KeyError, TypeError, ValueError):
        return None
    if t is None or row.get("rssi") is None or row.get("snr") is None:
        return None
    out = {"member": member, "counter": counter, "type": OLD_TYPES.get(kind, kind), "hops": hops, "via": via,
           "rssi": int(row["rssi"]), "snr": row["snr"], "decision": decision, "routed": bool(d.get("routed")),
           "path": path, "sender": p.member_label(member), "time": t}
    a = d.get("announce")
    if isinstance(a, dict) and "region" in a and "version" in a:
        out["announce"] = {"region": a["region"], "version": a["version"]}
    return out


async def restore(db, source_name: str, *, before: float, messages: bool, statuses: bool) -> dict[str, Any]:
    """activity, heard, announces, statuses, messages and sent, for listener.apply_restored()."""
    out: dict[str, Any] = {"activity": [], "heard": {}, "announces": {}, "statuses": {}, "messages": [], "sent": []}

    async def rows(sql: str, params: tuple = ()) -> list[dict]:
        try:
            return [dict(r) for r in await db.fetch_all(sql, params)]
        except Exception:
            logger.debug("localmesh: could not read what was stored", exc_info=True)
            return []

    cols = "source_id, decoded_payload, rssi, snr, timestamp"
    mine = "protocol = 'localmesh' AND capture_source = ? AND timestamp < ?"   # the core stores UTC ISO text
    args = (source_name, datetime.fromtimestamp(before, timezone.utc).isoformat())
    for r in await rows(f"SELECT {cols} FROM packets WHERE {mine} ORDER BY id DESC LIMIT {ACTIVITY}", args):
        e = packet_entry(r)
        if e:
            out["activity"].append(e)
    out["activity"].reverse()   # newest last

    for r in await rows(f"SELECT p.source_id, p.decoded_payload, p.rssi, p.snr, p.timestamp, x.n FROM packets p JOIN "
                        f"(SELECT MAX(id) AS id, COUNT(*) AS n FROM packets WHERE {mine} GROUP BY source_id) x "
                        "ON p.id = x.id", args):
        e = packet_entry(r)
        if e:
            out["heard"][e["member"]] = {"count": r["n"], "last_heard": e["time"], **{k: e[k] for k in (
                "type", "hops", "via", "rssi", "snr")}, "heard_directly": p.heard_directly(
                    e["routed"], e["path"], e["via"], e["member"])}

    for r in await rows(f"SELECT {cols} FROM packets WHERE id IN (SELECT MAX(id) FROM packets WHERE {mine} "
                        "AND packet_type = 'localmesh_announce' AND decoded_payload LIKE '%\"announce\": {%' "
                        "GROUP BY source_id)", args):
        e = packet_entry(r)
        if e and "announce" in e and e["decision"] in ("not_member", "forward"):
            out["announces"][e["member"] - p.REPEATER_MEMBER_BASE] = {**e["announce"], "time": e["time"]}

    if statuses:
        for r in await rows("SELECT node, data FROM lmr_latest"):
            st = _status(r.get("data"))
            if st:
                out["statuses"][int(r["node"])] = st
                for e in out["activity"]:   # its popup too, when that packet is still in the list
                    if (e["type"], e["member"], e["counter"]) == ("status", p.REPEATER_MEMBER_BASE | int(r["node"]), st["counter"]):
                        e["repeater_status"] = {k: v for k, v in st.items() if k != "counter"}

    if messages:
        sql = "SELECT text, node_id, node_name, channel, timestamp, status, packet_id, rssi, snr FROM messages " \
              "WHERE protocol = 'localmesh' AND direction = ? ORDER BY id DESC LIMIT ?"
        out["messages"] = [m for m in map(_received, await rows(sql, ("received", MESSAGES))) if m][::-1]
        out["sent"] = [m for m in map(_sent, await rows(sql, ("sent", SENT))) if m][::-1]
    return out


def _status(text: Any) -> Optional[dict]:
    """What localmesh itself would have kept of a status (repeater_status.read, plus time and counter)."""
    keys = ("battery_mv", "noise_floor", "uptime_h", "airtime_pct", "queue", "rx_ok", "rx_rejected", "version",
            "neighbor_count", "neighbors", "reasons", "time", "counter")
    try:
        d = json.loads(text)
        st = {k: d[k] for k in keys}
        st["neighbors"] = [{"node": int(n["node"]), "snr": n["snr"]} for n in st["neighbors"]]
        return st
    except (KeyError, TypeError, ValueError):
        return None


def _received(r: dict) -> Optional[dict]:
    try:
        _, member, channel, counter = str(r["packet_id"]).split(":")   # listener._note_message
        m = {"member": int(member), "channel": int(channel), "counter": int(counter)}
    except (KeyError, ValueError):
        return None
    t = unix(r.get("timestamp"))
    if t is None:
        return None
    return {**m, "index": None, "time": None, "hops": None, "via": None, "rssi": r.get("rssi"), "snr": r.get("snr"),
            "text": r["text"], "path": [], "received": t, "restored": True}


def _sent(r: dict) -> Optional[dict]:
    t = unix(r.get("timestamp"))
    node_id = str(r.get("node_id") or "")
    direct = not node_id.startswith("broadcast:")
    try:
        number = int(node_id.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None
    if t is None:
        return None
    return {"time": t, "direct": direct, "channel": p.CHANNEL_DIRECT if direct else number, "to": number if direct else None,
            "text": r["text"], "counter": None, "airtime_ms": None, "status": r.get("status") or "sent"}
