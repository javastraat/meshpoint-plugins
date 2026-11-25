"""Capture source for a LocalMesh receiver (Heltec V4 with companion firmware) on USB serial.

Speaks the binary companion protocol (SLIP frames, see protocol.py). At connect: HELLO (the reply is the receiver's
INFO), STATUS, CHANNELS_GET, MESSAGES_GET (the board's last 32 messages, so the page has them after a Meshpoint
restart), MEMBERS_GET (page by page) and NEIGHBORS_GET; then it listens, and asks for the neighbours every minute and
the member list every 15 minutes. One command at a time, as the protocol asks.

Every packet the receiver hears arrives as an RX_LOG event and goes into Meshpoint's packet pipeline and the Activity
list; STATUS_EVENTs (every 30 s) keep the topbar chip current; MESSAGE, ADVERT and CHANNEL_EVENT feed the page.

A repeater's announce (region, firmware) is plain text over the air, but RX_LOG has only the header. RAWLOG SHORT makes
the board print those packets in full, as TEXT events just before their RX_LOG. The same goes for a repeater's status,
which is encrypted: it is opened when the Settings tab has a status key (repeater_status.py). Not saved on the board, so it is sent
again every minute (after a restart of the board it is off).

By itself this source only sends the read commands above (and RAWLOG, which changes only what the board reports). It transmits only when the Send tab asks (an admin, through
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
import re
import threading
import time
from dataclasses import asdict
from typing import Any, AsyncIterator, Optional

from src.capture.base import CaptureSource
from src.models.packet import RawCapture
from src.models.signal import SignalMetrics

from . import hooks
from . import protocol as p
from . import repeater_status
from . import restore
from . import state
from . import views

logger = logging.getLogger(__name__)

RECONNECT_S = 10
REPLY_TIMEOUT_S = 5
NEIGHBORS_EVERY_S = 60
MEMBERS_EVERY_S = 15 * 60
_HEX = re.compile(r"^[0-9a-fA-F]+$")
PLAUSIBLE_TIME = 1_600_000_000   # a board without a set clock reports small numbers

# sequence number per command, so a reply is recognised by its number alone
SEQ_HELLO, SEQ_STATUS, SEQ_CHANNELS, SEQ_MESSAGES, SEQ_MEMBERS, SEQ_NEIGHBORS, SEQ_NAMES, SEQ_REPEATERS, SEQ_TIME, SEQ_RAWLOG = (
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
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
        self.names: dict[int, str] = {}          # what the board remembers per member (NAMES_GET), also from before a restart
        self._names_next: dict[int, str] = {}
        self.repeaters: dict[int, dict] = {}     # node -> region, flags, name (REPEATERS_GET and REPEATER events)
        self._repeaters_next: dict[int, dict] = {}
        self.announces: dict[int, dict] = {}     # node -> region, version, time of its last announce heard
        self.statuses: dict[int, dict] = {}      # node -> its last status we could open (repeater_status.read), time
        self._raw, self._raw_open = "", False     # hex of the last RAW line, waiting for its RX_LOG
        self.channels: dict[int, str] = {}
        self._text_names: dict[int, str] = {}   # member -> the name last seen in front of its channel messages
        self.neighbors: list[dict] = []
        self.sent: collections.deque = collections.deque(maxlen=100)   # what the Send tab sent, with its status
        # The name on the network. Firmware 13-11 reports it in INFO (the board is the truth then, name_from_board); with
        # older firmware it is the copy this plugin last set, kept in the config (lost when the plugin is uninstalled).
        self.net_name = name
        self.name_from_board = False
        # Set by backend/__init__.py's wire() once the shared core database is
        # available (build() runs before it). None until then, and in any test
        # that never sets it -- every write through it below is a best-effort
        # no-op without it, same "fails open" shape as the rest of this file.
        self.message_repo = None
        self._started: Optional[float] = None   # start(): the database is read up to here, the rest came in live
        self._restore_task: Optional[asyncio.Task] = None
        self.ws_manager = None   # also set in wire() -- see that file's own comment on why
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
    def is_running(self) -> bool:
        """For Meshpoint's status LED: the port is open (not while the cable is out and we keep trying)."""
        return self._running and self._connected

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
            "name_from_board": self.name_from_board,
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
                "names": dict(self.names), "repeaters": {k: dict(v) for k, v in self.repeaters.items()},
                "statuses": {k: dict(v) for k, v in self.statuses.items()},
                "announces": {k: dict(v) for k, v in self.announces.items()},
                "neighbors": [dict(n) for n in self.neighbors],
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
        self._loop = asyncio.get_running_loop()
        self._started = time.time()
        self._running = True
        self._thread = threading.Thread(target=self._run, name=f"{self.name}-reader", daemon=True)
        self._thread.start()
        logger.info("%s: started on %s @ %d baud", self.name, self._port, self._baud)

    def restore_from(self, db, *, messages: bool) -> None:
        """From wire(): fill the lists with what the database still has. The source already runs by then."""
        self._restore_task = asyncio.ensure_future(self._restore(db, messages))

    async def _restore(self, db, messages: bool) -> None:
        try:
            before = self._started if self._started is not None else time.time()
            self.apply_restored(await restore.restore(db, self.name, before=before, messages=messages,
                                                      statuses=bool(state.status_keys())))
        except Exception:
            logger.warning("%s: could not restore the lists from the database", self.name, exc_info=True)

    def apply_restored(self, r: dict) -> None:
        """Older than anything that came in live meanwhile: behind it in the lists, and never over it."""
        with self._lock:
            for e in reversed(r["activity"]):
                if len(self.activity) < self.activity.maxlen:
                    self.activity.appendleft(e)
            for member, h in r["heard"].items():
                live = self.heard.get(member)
                self.heard[member] = {**live, "count": live["count"] + h["count"]} if live else h
            for node, a in r["announces"].items():
                self.announces.setdefault(node, a)
            for node, st in r["statuses"].items():
                self.statuses.setdefault(node, st)
            for x in reversed(r["sent"]):
                if len(self.sent) < self.sent.maxlen:
                    self.sent.appendleft(x)
            for m in reversed(r["messages"]):
                key = (m["member"], m["counter"], m["channel"])
                if key not in self._message_keys and len(self.messages) < self.messages.maxlen:
                    self._message_keys.add(key)
                    self.messages.appendleft(m)
            if self.last_heard is None and self.heard:
                self.last_heard = max(h["last_heard"] for h in self.heard.values())

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
                # A board without a set clock stamps no time on its messages, so a restart of Meshpoint could not tell
                # when a member was last heard. The app does the same after HELLO; skipped if this host has no clock.
                if time.time() >= PLAUSIBLE_TIME:
                    self._queue_cmd(SEQ_TIME, p.time_set(SEQ_TIME, int(time.time())))
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
        await self._save_sent(entry)
        return dict(entry)

    async def _save_sent(self, entry: dict) -> None:
        """Mirror into the core Messages page's shared store (protocol="localmesh"), same node_id
        scheme as _save_received_async: "localmesh:<member>" for a direct message, so the thread
        matches the one that message's own replies arrive under; "broadcast:localmesh:<channel>"
        for a channel message, the same convention _resolve_node_id() in core's messages.py already
        uses for meshtastic/meshcore. Called from send_text(), already on the event loop (unlike the
        receive path below, there's no background-thread marshaling to do here) -- best-effort, a
        storage hiccup here never fails the send itself, which has already succeeded by this point.
        """
        if self.message_repo is None:
            return
        if entry["direct"]:
            node_id = f"localmesh:{entry['to']}"
            name = (self.adverts.get(entry["to"]) or {}).get("name") or p.member_label(entry["to"])
            channel = 0
        else:
            node_id = f"broadcast:localmesh:{entry['channel']}"
            # Meshpoint-side override (Settings tab) first, then whatever
            # name the receiver itself reports for this channel slot, then
            # the bare number -- same preference order as views.py's own
            # messages()/channel_name field.
            name = (state.channel_name(entry["channel"]) or self.channels.get(entry["channel"])
                    or f"channel {entry['channel']}")
            channel = entry["channel"]
        try:
            await self.message_repo.save_sent(
                text=entry["text"], node_id=node_id, node_name=name, protocol="localmesh",
                channel=channel, status="sent",
            )
        except Exception:
            logger.debug("%s: could not mirror a sent message into the core Messages store",
                         self.name, exc_info=True)
            return
        # Matches the reticulum plugin's own lxmf_service.py send paths --
        # the sender's own browser tab already renders optimistically from
        # the HTTP response (messaging.js's _sendLocalMeshMessage()), this
        # is for a *second* session watching the same conversation to see
        # it live too.
        if self.ws_manager is not None:
            try:
                await self.ws_manager.broadcast("message_sent", {
                    "text": entry["text"], "node_id": node_id, "node_name": name,
                    "protocol": "localmesh", "direction": "sent",
                })
            except Exception:
                logger.debug("%s: could not broadcast message_sent", self.name, exc_info=True)

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
            # HELLO again first: firmware 0.10.4+ stops pushing events (messages, RX_LOG) over USB once it thinks the host
            # went away (a USB pause of a few ms on the Pi is enough) and only starts again on HELLO. Commands keep
            # working meanwhile, so without this the page silently stops receiving. The reply is INFO, as at connect.
            self._queue_cmd(SEQ_HELLO, p.hello(seq=SEQ_HELLO))
            self._queue_cmd(SEQ_NEIGHBORS, p.command(p.NEIGHBORS_GET, SEQ_NEIGHBORS))
            self._queue_cmd(SEQ_RAWLOG, p.raw_command(SEQ_RAWLOG, "RAWLOG SHORT"))
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

    def _queue_names(self) -> None:
        self._names_next = {}
        self._queue_cmd(SEQ_NAMES, p.names_get(SEQ_NAMES, 0))

    def _queue_repeaters(self) -> None:
        self._repeaters_next = {}
        self._queue_cmd(SEQ_REPEATERS, p.repeaters_get(SEQ_REPEATERS, 0))

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
                elif seq == SEQ_NAMES:   # firmware from before 14-11 does not know NAMES_GET: go on with the repeaters
                    self._queue_repeaters()
            elif t == p.STATUS_EVENT:
                self.status = p.parse_status(payload)
            elif t == p.TEXT:
                self._note_text(p.parse_text(payload))
            elif t == p.RX_LOG:
                now = time.time()
                self.last_heard = now
                log = p.parse_rx_log(payload)
                info = self._note_rx(log, now)
                if self._loop is not None:   # an announce goes along; a status does not: it is sealed on the air
                    extra = {"announce": info["announce"]} if "announce" in info else None
                    self._loop.call_soon_threadsafe(self._enqueue, payload, log.rssi, log.snr, extra)
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
            elif t == p.REPEATER_EVENT:
                r = p.parse_repeater_event(payload)
                with self._lock:
                    self.repeaters[r.node] = asdict(r) | {"revoked": r.revoked}
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
            if self.info.name is not None:
                self.net_name = self.info.name
                self.name_from_board = True
        elif seq == SEQ_STATUS:
            self.status = p.parse_status(payload)
        elif seq == SEQ_CHANNELS:
            channels = p.parse_channels(payload)
            with self._lock:
                self.channels = channels
        elif seq == SEQ_NEIGHBORS:
            now = time.time()   # ago_s counts from here, not from when the page asks
            self.neighbors = [{**asdict(n), "at": now} for n in p.parse_neighbors(payload)]
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
                self._queue_names()   # the names, then the repeaters, follow the member list one command at a time
        elif seq == SEQ_NAMES:   # older firmware answers with an error, which never gets here
            total, page = p.parse_names(payload)
            self._names_next.update(page)
            if page and len(self._names_next) < total:
                self._queue_cmd(SEQ_NAMES, p.names_get(SEQ_NAMES, len(self._names_next)))
            else:
                with self._lock:
                    self.names = self._names_next
                self._names_next = {}
                self._queue_repeaters()
        elif seq == SEQ_REPEATERS:
            total, page = p.parse_repeaters(payload)
            for r in page:
                self._repeaters_next[r.node] = asdict(r) | {"revoked": r.revoked}
            if page and len(self._repeaters_next) < total:
                self._queue_cmd(SEQ_REPEATERS, p.repeaters_get(SEQ_REPEATERS, len(self._repeaters_next)))
            else:
                with self._lock:
                    self.repeaters = self._repeaters_next
                self._repeaters_next = {}
        # SEQ_MESSAGES: the reply is only the total; the messages follow as MESSAGE events

    def _sent_status(self, counter: int, direct: bool, status: str, only_from: Optional[str] = None) -> None:
        with self._lock:
            for e in self.sent:
                if e["counter"] == counter and e["direct"] == direct and (only_from is None or e["status"] == only_from):
                    e["status"] = status

    def _note_text(self, line: str) -> None:
        """The board's log lines; only a whole packet (RAW <hex>) matters here. A long one comes in several TEXT events,
        one right after the other; its RX_LOG follows."""
        if line.startswith("RAW "):
            self._raw, self._raw_open = line[4:], True
        elif self._raw_open and len(self._raw) < 600 and _HEX.match(line):
            self._raw += line
        else:
            self._raw_open = False

    def _take_raw(self, log: p.RxLog) -> Optional[bytes]:
        """The packet of the last RAW line, if it is the one this RX_LOG is about."""
        raw, self._raw, self._raw_open = self._raw, "", False
        try:
            frame = bytes.fromhex(raw)
        except ValueError:
            return None
        k = p.parse_repeater_packet(frame)
        return frame if k and (k.type, k.member, k.counter) == (log.type, log.member, log.counter) else None

    def _repeater_info(self, log: p.RxLog) -> dict:
        """What a repeater's announce, or its status when we have the status key, says; unverified."""
        frame = self._take_raw(log)
        if frame is None:
            return {}
        if log.type == "announce":
            a = p.parse_announce(frame)
            return {"announce": {"region": a.region, "version": a.version}} if a else {}
        if log.type == "status":
            k = p.parse_repeater_packet(frame)
            st = repeater_status.open_status(state.status_keys(), k.member, k.counter, k.payload)
            return {"repeater_status": st} if st else {}
        return {}

    def _note_rx(self, log: p.RxLog, now: float) -> dict:
        """Returns what the packet itself said, if we know it (_repeater_info)."""
        if self.info and log.member == self.info.member:   # our own channel message, heard back from a repeater
            self._sent_status(log.counter, False, "repeated", only_from="sent")
        entry = {**asdict(log), "path": [{"node": n, "snr": s} for n, s in log.path], "sender": p.member_label(log.member), "time": now}
        info = self._repeater_info(log)
        entry.update(info)
        with self._lock:
            # not_member: what a companion says of every repeater packet of its own net (it cannot check them)
            if log.decision in ("not_member", "forward"):
                node = log.member - p.REPEATER_MEMBER_BASE
                if "announce" in info:
                    self.announces[node] = {**info["announce"], "time": now}
                if "repeater_status" in info:
                    self.statuses[node] = {**info["repeater_status"], "time": now}
                    if self._loop is not None:   # for plugins that build on this one (localmesh-repeaters)
                        known = self.repeaters.get(node) or {}
                        self._loop.call_soon_threadsafe(hooks.emit_repeater_status, self, {
                            **info["repeater_status"], "node": node, "counter": log.counter, "time": now,
                            "name": known.get("name", ""), "region": known.get("region"), "hops": log.hops,
                            "via": log.via, "rssi": log.rssi, "snr": log.snr})
            self.activity.append(entry)
            h = self.heard.setdefault(log.member, {"count": 0})
            h.update(count=h["count"] + 1, last_heard=now, type=log.type, hops=log.hops, via=log.via,
                     rssi=log.rssi, snr=log.snr)
        return info

    def _note_message(self, m: p.Message, now: float) -> None:
        """Keep a message once: the board's buffer is fetched again at every connect."""
        key = (m.member, m.counter, m.channel)
        with self._lock:
            if key in self._message_keys:
                # from the database after a restart (restore.py): the board knows more (its time, the route)
                for i, old in enumerate(self.messages):
                    if old.get("restored") and (old["member"], old["counter"], old["channel"]) == key:
                        self.messages[i] = {**asdict(m), "received": old["received"],
                                            "time": m.time if m.time >= PLAUSIBLE_TIME else None}
                return
            if len(self.messages) == self.messages.maxlen:
                old = self.messages[0]
                self._message_keys.discard((old["member"], old["counter"], old["channel"]))
            self._message_keys.add(key)
            record = {**asdict(m), "received": now, "time": m.time if m.time >= PLAUSIBLE_TIME else None}
            self.messages.append(record)
        # Plugins that build on this one (localmesh-bot) get every new message, on the event loop (hooks.py)
        if self._loop is not None:
            self._loop.call_soon_threadsafe(hooks.emit_message, self, record)
        # Mirror into the core Messages page's shared store too (protocol="localmesh"), so LocalMesh
        # conversations show up there alongside Reticulum/Meshtastic/MeshCore -- same deduplicate-
        # once reasoning as self.messages above, just a second destination for the same event. This
        # runs on the background reader thread (_handle()'s caller), not the event loop -- same
        # thread-marshaling this file already does for RX_LOG's own _enqueue() a few lines up.
        if self._loop is not None and self.message_repo is not None:
            direct = m.channel == p.CHANNEL_DIRECT
            node_id = f"localmesh:{m.member}" if direct else f"broadcast:localmesh:{m.channel}"
            # The row's name is the sender: the Messages page shows it above each message. A channel's own title
            # comes from GET /api/localmesh/channels instead (a stored row has no room for both). Same order as the
            # LocalMesh page: advert name, else the "Name: " the app puts in front of a channel message, else the number.
            text = m.text
            prefix, rest = views.name_prefix(text)
            if prefix and not direct and 0 < m.member < p.REPEATER_MEMBER_BASE:
                self._text_names[m.member] = prefix
            name = (self.adverts.get(m.member) or {}).get("name") or self._text_names.get(m.member) \
                or p.member_label(m.member)
            if not direct and prefix == name:
                text = rest   # the name is already shown above the message
            # The board's buffer comes back at every connect (see above); the core store only dedupes on packet_id.
            packet_id = f"localmesh:{m.member}:{m.channel}:{m.counter}"
            self._loop.call_soon_threadsafe(
                self._schedule_save_received, text, node_id, name, m.channel if not direct else 0, m.rssi, m.snr,
                packet_id)

    def _schedule_save_received(self, text, node_id, node_name, channel, rssi, snr, packet_id) -> None:
        """call_soon_threadsafe can only schedule a plain callback, not await a coroutine directly --
        this is that callback, run on the event loop, whose only job is to hand the actual async
        repo write off to the loop as its own task."""
        asyncio.ensure_future(self._save_received_async(text, node_id, node_name, channel, rssi, snr, packet_id))

    async def _save_received_async(self, text, node_id, node_name, channel, rssi, snr, packet_id) -> None:
        if self.message_repo is None:
            return
        try:
            _row_id, is_duplicate = await self.message_repo.save_received(
                text=text, node_id=node_id, node_name=node_name, protocol="localmesh",
                channel=channel, rssi=rssi, snr=snr, packet_id=packet_id,
            )
        except Exception:
            logger.debug("%s: could not mirror a message into the core Messages store", self.name, exc_info=True)
            return
        if is_duplicate or self.ws_manager is None:
            return
        # The *generic* event name, not a localmesh-specific one -- this is
        # what actually drives a live sidebar badge/notifier-sound/open-
        # thread append. (The reticulum plugin broadcasts its own
        # "reticulum_message" event instead, which turns out not to be
        # wired into message_notifier.js or messages_unread_badge.js at
        # all -- both only listen for "message_received" -- so Reticulum's
        # own live notifications have the same gap this fixes for
        # LocalMesh, just not fixed here too.)
        try:
            await self.ws_manager.broadcast("message_received", {
                "text": text, "node_id": node_id, "node_name": node_name,
                "protocol": "localmesh", "direction": "received",
                **({"rssi": rssi} if rssi is not None else {}),
                **({"snr": snr} if snr is not None else {}),
            })
        except Exception:
            logger.debug("%s: could not broadcast message_received", self.name, exc_info=True)

    def _enqueue(self, payload: bytes, rssi: int, snr: float, extra: Optional[dict] = None) -> None:
        try:
            self._queue.put_nowait(RawCapture(payload=payload, signal=self.signal(rssi, snr), capture_source=self.name,
                                              pre_decoded=extra))
        except asyncio.QueueFull:
            logger.warning("%s: queue full, dropping a packet", self.name)
