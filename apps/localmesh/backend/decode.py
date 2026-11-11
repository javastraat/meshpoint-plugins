"""Turn a LocalMesh RX_LOG (one packet the receiver heard) into a Meshpoint Packet for the packet log.

LocalMesh is not one of Meshpoint's built-in protocols, so this uses OpenProtocol / OpenPacketType, the same way the
DAPNET plugin does. The packet contents stay encrypted (the receiver only reports the header and its decision), so
``decoded_payload`` holds what the receiver knows about it: type, member, counter, hops, previous hop, path and the
decision (forward, deliver, replay, blocked, ...).
"""

from __future__ import annotations

import uuid
from typing import Optional

from src.models.packet import OpenPacketType, OpenProtocol, Packet
from src.models.signal import SignalMetrics

from . import protocol as p


def adapt_rx_log(payload: bytes, signal: Optional[SignalMetrics] = None) -> Optional[Packet]:
    try:
        log = p.parse_rx_log(payload)
    except ValueError:
        return None
    decoded = {
        "type": log.type, "member": log.member, "sender": p.member_label(log.member), "counter": log.counter,
        "hops": log.hops, "via": log.via, "decision": log.decision, "routed": log.routed,
        "path": [{"node": n, "snr": s} for n, s in log.path],
    }
    return Packet(
        packet_id=uuid.uuid4().hex[:16],
        source_id=p.member_label(log.member),
        destination_id="broadcast",
        protocol=OpenProtocol("localmesh"),
        packet_type=OpenPacketType(f"localmesh_{log.type}"),
        decoded_payload=decoded,
        decrypted=False,
        signal=signal,
    )
