"""Capture source for a LocalMesh receiver (Heltec V4 with companion firmware) on USB serial.

Speaks the binary companion protocol (SLIP frames, see protocol.py). At connect: HELLO (the reply is the receiver's
INFO), STATUS, CHANNELS_GET, MESSAGES_GET (the board's last 32 messages, so the page has them after a Meshpoint
restart), MEMBERS_GET (page by page) and NEIGHBORS_GET; then it listens, and asks for the neighbours every minute and
the member list every 15 minutes. One command at a time, as the protocol asks.

Every packet the receiver hears arrives as an RX_LOG event and goes into Meshpoint's packet pipeline and the Activity
list; STATUS_EVENTs (every 30 s) keep the topbar chip current; MESSAGE, ADVERT and CHANNEL_EVENT feed the page.

By itself this source only sends the read commands above. It transmits only when the Send tab asks (an admin, through
send_text / set_name / send_advert): those wait for the receiver's reply, go through the same one-at-a-time queue, and
the receiver's own airtime budget still decides. DELIVERY / DELIVERY_FAILED and our own packets heard back (RX_LOG)
update the status of what was sent.

Same threading shape as the DAPNET plugin's source: pyserial blocks, so a background thread owns the serial port and
hands results to asyncio with call_soon_threadsafe. Unlike DAPNET it reopens the port by itself when the cable comes
out or the board restarts, and greets the board again.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import threading
import time
from dataclasses import asdict
from typing import Any, AsyncIterator, Optional

from src.capture.base import CaptureSource
from src.models.packet import RawCapture
from src.models.signal import SignalMetrics

from . import protocol as p

logger = logging.getLogger(__name__)

RECONNECT_S = 10
REPLY_TIMEOUT_S = 5
NEIGHBORS_EVERY_S = 60
MEMBERS_EVERY_S = 15 * 60
PLAUSIBLE_TIME = 1_600_000_000   # a board without a set clock reports small numbers

# sequence number per command, so a reply is recognised by its number alone
SEQ_HELLO, SEQ_STATUS, SEQ_CHANNELS, SEQ_MESSAGES, SEQ_MEMBERS, SEQ_NEIGHBORS = 1, 2, 3, 4, 5, 6
SEQ_REQUEST_FIRST = 16   # 16..255: commands someone awaits (request())


class CommandError(Exception):
    """The receiver answered a command with an error code (protocol.ERR_*), or did not answer."""

    def __init__(self, code: Optional[int]) -> None:
        super().__init__(f"receiver error {code}" if code is not None else "receiver did not answer")
        self.code = code


class LocalMeshSerialSource(CaptureSource):
    def __init__(self, serial_port: Optional[str] = None, serial_baud: int = 115200, label: str = "",
                 name: Optional[str] = None) -> None:
        self._port = serial_port
        self._baud = serial_baud
        self._label = label
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._running = False
        self._connected = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._serial = None
        self.info: Optional[p.Info] = None
        self.status: Optional[p.Status] = None
        self.last_heard: Optional[float] = None
        # for the plugin's page; lists newest last. The lock keeps a route's copy from racing the reader thread.
        self._lock = threading.Lock()
        self.messages: collections.deque = collections.deque(maxlen=300)
        self._message_keys: set = set()
        self.activity: collections.deque = collections.deque(maxlen=300)
        self.adverts: dict[int, dict] = {}
        self.heard: dict[int, dict] = {}         # member -> what RX_LOG told us: count, last time/type/hops/signal
        self.members: dict[int, dict] = {}       # the receiver's member list (MEMBERS_GET)
        self._members_next: dict[int, dict] = {}
        self.channels: dict[int, str] = {}
        self.neighbors: list[dict] = []
        self.sent: collections.deque = collections.deque(maxlen=100)   # what the Send tab sent, with its status
        self.net_name = name   # the name on the network this plugin last set (the board does not report it); in the config
        # command queue: the reader thread sends; request() adds from the event loop (deque append is thread-safe)
        self._pending: collections.deque = collections.deque()
        self._futures: dict[int, asyncio.Future] = {}
        self._next_seq = SEQ_REQUEST_FIRST
        self._waiting: Optional[tuple[int, float]] = None
        self._next_neighbors = self._next_members = 0.0

    @property
    def name(self) -> str:
        return f"localmesh_{self._label}" if self._label else "localmesh"

    @property
    def connected(self) -> bool:
        return self._connected

    def snapshot(self) -> dict[str, Any]:
        """For GET /api/localmesh/status and the topbar chip."""
        return {
            "name": self.name, "label": self._label, "serial_port": self._port, "connected": self._connected,
            "info": asdict(self.info) if self.info else None,
            "status": asdict(self.status) if self.status else None,
            "last_heard": self.last_heard,
            "net_name": self.net_name,
            "channels": {str(k): v for k, v in sorted(self.channels.items())},
            "neighbors": list(self.neighbors),
        }

    def page_data(self) -> dict[str, Any]:
        """Copies of what the page lists, for the routes (taken under the lock)."""
        with self._lock:
            return {
                "messages": list(self.messages), "activity": list(self.activity),
                "adverts": {k: dict(v) for k, v in self.adverts.items()},
                "heard": {k: dict(v) for k, v in self.heard.items()},
                "members": {k: dict(v) for k, v in self.members.items()},
                "channels": dict(self.channels),
                "sent": [dict(x) for x in self.sent],
                "own_member": self.info.member if self.info else None,
                "net_name": self.net_name,
            }

    def signal(self, rssi: float, snr: float) -> SignalMetrics:
        i = self.info
        return SignalMetrics(rssi=float(rssi), snr=float(snr), frequency_mhz=i.freq_mhz if i else 869.556,
                             spreading_factor=i.sf if i else 7, bandwidth_khz=i.bw_khz if i else 62.5,
                             coding_rate=f"4/{i.cr}" if i else "4/5")

    async def start(self) -> None:
        if not self._port:
            logger.warning("%s: no serial_port configured, source will not run", self.name)
            return
        self._running = True
        self._loop = asyncio.get_running_loop()
        self._thread = threading.Thread(target=self._run, name=f"{self.name}-reader", daemon=True)
        self._thread.start()
        logger.info("%s: started on %s @ %d baud", self.name, self._port, self._baud)

    async def stop(self) -> None:
        self._running = False
        self._close()

    async def packets(self) -> AsyncIterator[RawCapture]:
        while self._running:
            try:
                yield await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

    # --- background thread ---

    def _close(self) -> None:
        self._connected = False
        if self._loop is not None:   # whoever waits for a reply gets "did not answer" instead of hanging
            for seq in list(self._futures):
                self._loop.call_soon_threadsafe(self._resolve, seq, None, b"")
        s, self._serial = self._serial, None
        if s is not None:
            try:
                s.close()
            except Exception:
                pass

    def _run(self) -> None:
        """Open, greet, read; on any error close and try again after RECONNECT_S."""
        while self._running:
            try:
                import serial
                self._serial = serial.Serial(self._port, self._baud, timeout=1.0)
                self._connected = True
                self._pending.clear(); self._waiting = None
                self._queue_cmd(SEQ_HELLO, p.hello(seq=SEQ_HELLO))
                self._queue_cmd(SEQ_STATUS, p.command(p.STATUS, SEQ_STATUS))
                self._queue_cmd(SEQ_CHANNELS, p.command(p.CHANNELS_GET, SEQ_CHANNELS))
                self._queue_cmd(SEQ_MESSAGES, p.messages_get(SEQ_MESSAGES, 0))   # the board keeps its last 32
                self._next_neighbors = self._next_members = 0.0
                decoder = p.SlipDecoder()
                while self._running:
                    data = self._serial.read(512)
                    if data:
                        frames, _lines = decoder.feed(data)   # text lines are the board's log: not for us
                        for f in frames:
                            self._handle(f)
                    self._tick(time.monotonic())
            except Exception as e:
                if self._running:
                    logger.warning("%s: %s; retrying in %d s", self.name, e, RECONNECT_S)
            self._close()
            for _ in range(RECONNECT_S):
                if not self._running:
                    return
                time.sleep(1)

    # --- commands someone waits for (the Send tab) ---

    async def request(self, build, timeout: float = REPLY_TIMEOUT_S + 5) -> bytes:
        """Queue the command build(seq) and wait for its reply payload. Raises CommandError on an error code or no
        answer, ConnectionError when the receiver is not connected."""
        if not self._connected or self._loop is None:
            raise ConnectionError("receiver not connected")
        seq = self._take_seq()
        fut = self._loop.create_future()
        self._futures[seq] = fut
        self._queue_cmd(seq, build(seq))
        try:
            err, payload = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise CommandError(None) from None
        finally:
            self._futures.pop(seq, None)
        if err != p.ERR_OK:
            raise CommandError(err)
        return payload

    def _take_seq(self) -> int:
        for _ in range(256):
            seq = self._next_seq
            self._next_seq = seq + 1 if seq < 255 else SEQ_REQUEST_FIRST
            if seq not in self._futures:
                return seq
        raise CommandError(None)   # 240 commands waiting: something is badly stuck

    def _resolve(self, seq: int, err: Optional[int], payload: bytes) -> None:
        fut = self._futures.get(seq)
        if fut is not None and not fut.done():
            fut.set_result((err, payload))

    async def send_text(self, text: str, *, channel: Optional[int] = None, member: Optional[int] = None) -> dict:
        """A channel message (channel=) or a direct message (member=). Returns the entry added to self.sent."""
        if member is not None:
            reply = await self.request(lambda seq: p.send_direct(seq, member, text))
        else:
            reply = await self.request(lambda seq: p.send_channel(seq, channel, text))
        s = p.parse_sent(reply)
        entry = {"time": time.time(), "direct": member is not None, "channel": p.CHANNEL_DIRECT if member is not None
                 else channel, "to": member, "text": text, "counter": s.counter, "airtime_ms": s.airtime_ms,
                 "status": "sent"}
        with self._lock:
            self.sent.append(entry)
        return dict(entry)

    async def set_name(self, name: str) -> None:
        """The receiver's name on the network (stored in its flash). A changed name makes the board broadcast an
        advert by itself 5 s later; it also goes in front of every channel message it sends."""
        await self.request(lambda seq: p.advert_set(seq, name))
        self.net_name = p.clip_utf8(name, p.NAME_MAX)

    async def send_advert(self) -> p.Sent:
        return p.parse_sent(await self.request(lambda seq: p.command(p.ADVERT_SEND, seq)))

    # --- commands: one at a time ---

    def _queue_cmd(self, seq: int, data: bytes) -> None:
        self._pending.append((seq, data))

    def _tick(self, now: float) -> None:
        """Queue the periodic requests, and send the next command once the previous one is answered (or timed out)."""
        if now >= self._next_neighbors:
            self._next_neighbors = now + NEIGHBORS_EVERY_S
            self._queue_cmd(SEQ_NEIGHBORS, p.command(p.NEIGHBORS_GET, SEQ_NEIGHBORS))
        if now >= self._next_members:
            self._next_members = now + MEMBERS_EVERY_S
            self._members_next = {}
            self._queue_cmd(SEQ_MEMBERS, p.members_get(SEQ_MEMBERS, 0))
        if self._waiting and now - self._waiting[1] > REPLY_TIMEOUT_S:
            logger.debug("%s: no reply to command %d", self.name, self._waiting[0])
            self._waiting = None
        if not self._waiting and self._pending and self._serial is not None:
            seq, data = self._pending.popleft()
            self._serial.write(data)
            self._waiting = (seq, now)

    def _handle(self, f: bytes) -> None:
        if len(f) < 2:
            return
        t, seq, payload = f[0], f[1], f[2:]
        try:
            if t == p.REPLY:
                if self._waiting and self._waiting[0] == seq:
                    self._waiting = None
                if seq in self._futures:   # someone awaits this one (request())
                    if self._loop is not None and payload:
                        self._loop.call_soon_threadsafe(self._resolve, seq, payload[0], payload[1:])
                elif payload and payload[0] == p.ERR_OK:
                    self._reply(seq, payload[1:])
            elif t == p.STATUS_EVENT:
                self.status = p.parse_status(payload)
            elif t == p.RX_LOG:
                now = time.time()
                self.last_heard = now
                log = p.parse_rx_log(payload)
                self._note_rx(log, now)
                if self._loop is not None:
                    self._loop.call_soon_threadsafe(self._enqueue, payload, log.rssi, log.snr)
            elif t == p.MESSAGE:
                self._note_message(p.parse_message(payload), time.time())
            elif t == p.ADVERT:
                a = p.parse_advert(payload)
                with self._lock:
                    self.adverts[a.member] = {**asdict(a), "heard": time.time()}
            elif t == p.DELIVERY:
                _by, counter = p.parse_delivery(payload)
                self._sent_status(counter, True, "delivered")
            elif t == p.DELIVERY_FAILED:
                _member, counter, _tries = p.parse_delivery_failed(payload)
                self._sent_status(counter, True, "failed")
            elif t == p.CHANNEL_EVENT:
                ev = p.parse_channel_event(payload)
                with self._lock:
                    if ev.removed:
                        self.channels.pop(ev.id, None)
                    else:
                        self.channels[ev.id] = ev.name
        except ValueError:
            logger.debug("%s: short or unknown frame 0x%02x ignored", self.name, t)

    def _reply(self, seq: int, payload: bytes) -> None:
        if seq == SEQ_HELLO:
            self.info = p.parse_info(payload)
        elif seq == SEQ_STATUS:
            self.status = p.parse_status(payload)
        elif seq == SEQ_CHANNELS:
            channels = p.parse_channels(payload)
            with self._lock:
                self.channels = channels
        elif seq == SEQ_NEIGHBORS:
            self.neighbors = [asdict(n) for n in p.parse_neighbors(payload)]
        elif seq == SEQ_MEMBERS:
            total, page = p.parse_members(payload)
            for m in page:
                self._members_next[m.id] = {**asdict(m), "blocked": m.blocked}
            if page and len(self._members_next) < total:
                self._queue_cmd(SEQ_MEMBERS, p.members_get(SEQ_MEMBERS, len(self._members_next)))
            else:
                with self._lock:
                    self.members = self._members_next
                self._members_next = {}
        # SEQ_MESSAGES: the reply is only the total; the messages follow as MESSAGE events

    def _sent_status(self, counter: int, direct: bool, status: str, only_from: Optional[str] = None) -> None:
        with self._lock:
            for e in self.sent:
                if e["counter"] == counter and e["direct"] == direct and (only_from is None or e["status"] == only_from):
                    e["status"] = status

    def _note_rx(self, log: p.RxLog, now: float) -> None:
        if self.info and log.member == self.info.member:   # our own channel message, heard back from a repeater
            self._sent_status(log.counter, False, "repeated", only_from="sent")
        entry = {**asdict(log), "sender": p.member_label(log.member), "time": now}
        with self._lock:
            self.activity.append(entry)
            h = self.heard.setdefault(log.member, {"count": 0})
            h.update(count=h["count"] + 1, last_heard=now, type=log.type, hops=log.hops, via=log.via,
                     rssi=log.rssi, snr=log.snr)

    def _note_message(self, m: p.Message, now: float) -> None:
        """Keep a message once: the board's buffer is fetched again at every connect."""
        key = (m.member, m.counter, m.channel)
        with self._lock:
            if key in self._message_keys:
                return
            if len(self.messages) == self.messages.maxlen:
                old = self.messages[0]
                self._message_keys.discard((old["member"], old["counter"], old["channel"]))
            self._message_keys.add(key)
            self.messages.append({**asdict(m), "received": now,
                                  "time": m.time if m.time >= PLAUSIBLE_TIME else None})

    def _enqueue(self, payload: bytes, rssi: int, snr: float) -> None:
        try:
            self._queue.put_nowait(RawCapture(payload=payload, signal=self.signal(rssi, snr), capture_source=self.name))
        except asyncio.QueueFull:
            logger.warning("%s: queue full, dropping a packet", self.name)
