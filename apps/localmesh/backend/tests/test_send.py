"""Tests for sending: the listener's request() (one awaited command through the queue), send_text and its status
updates, and the send routes' checks and error messages. No board: frames go straight into _handle()."""

from __future__ import annotations

import asyncio
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

from plugins.apps.localmesh.backend import listener as l
from plugins.apps.localmesh.backend import protocol as p
from plugins.apps.localmesh.backend import send_routes as routes
from plugins.apps.localmesh.backend import state
from src.api.audit import AuditLogWriter
from src.api.auth.jwt_session import SessionClaims


class FakePort:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def write(self, data: bytes) -> None:
        frames, _ = p.SlipDecoder().feed(data)
        self.frames += frames


def connected_source(label: str = "base") -> tuple[l.LocalMeshSerialSource, FakePort]:
    src = l.LocalMeshSerialSource(serial_port="/dev/null", label=label)
    port = FakePort()
    src._serial = port
    src._connected = True
    src._loop = asyncio.get_running_loop()
    src._next_neighbors = src._next_members = 1e12
    src.info = p.Info(1, "0.10.1", "Heltec V4", "companion", 100, 1, 3, "", True, 869.556, 7, 62.5, 5, 22)
    return src, port


async def answer(src, port, err: int = 0, payload: bytes = b"") -> int:
    """Let the queued command go out, then answer it like the board would. Returns its sequence number."""
    await asyncio.sleep(0)
    src._tick(0.0)
    seq = port.frames[-1][1]
    src._handle(bytes([p.REPLY, seq, err]) + payload)
    await asyncio.sleep(0)
    return seq


class TestListenerSend(unittest.TestCase):
    def test_channel_message_then_heard_back(self) -> None:
        async def run():
            src, port = connected_source()
            task = asyncio.create_task(src.send_text("hoi", channel=0))
            seq = await answer(src, port, payload=struct.pack("<IH", 42, 120))
            entry = await task
            self.assertGreaterEqual(seq, l.SEQ_REQUEST_FIRST)
            self.assertEqual(port.frames[-1], bytes([p.SEND, seq, 0, 3]) + b"hoi")
            self.assertEqual((entry["counter"], entry["airtime_ms"], entry["status"], entry["direct"]), (42, 120, "sent", False))
            # our own packet, passed on by a repeater: member 3 (us), counter 42
            src._handle(bytes([p.RX_LOG, 0]) + struct.pack("<IIBBHhbB", 3, 42, 0, 1, 101, -50, 8, 15))
            self.assertEqual(src.page_data()["sent"][0]["status"], "repeated")
        asyncio.run(run())

    def test_direct_message_delivered_and_failed(self) -> None:
        async def run():
            src, port = connected_source()
            for counter in (7, 8):
                task = asyncio.create_task(src.send_text("hallo", member=4))
                await answer(src, port, payload=struct.pack("<IH", counter, 90))
                await task
            src._handle(bytes([p.DELIVERY, 0]) + struct.pack("<II", 4, 7))
            src._handle(bytes([p.DELIVERY_FAILED, 0]) + struct.pack("<IIB", 4, 8, 3))
            self.assertEqual([(x["counter"], x["status"]) for x in src.page_data()["sent"]], [(7, "delivered"), (8, "failed")])
        asyncio.run(run())

    def test_error_code_raises(self) -> None:
        async def run():
            src, port = connected_source()
            task = asyncio.create_task(src.send_text("hoi", channel=5))
            await answer(src, port, err=p.ERR_STATE)
            with self.assertRaises(l.CommandError) as ctx:
                await task
            self.assertEqual(ctx.exception.code, p.ERR_STATE)
            self.assertEqual(list(src.sent), [])
        asyncio.run(run())

    def test_disconnect_while_waiting(self) -> None:
        async def run():
            src, port = connected_source()
            task = asyncio.create_task(src.send_advert())
            await asyncio.sleep(0)
            src._tick(0.0)
            src._close()
            with self.assertRaises(l.CommandError) as ctx:
                await task
            self.assertIsNone(ctx.exception.code)
        asyncio.run(run())

    def test_not_connected(self) -> None:
        async def run():
            src, _ = connected_source()
            src._connected = False
            with self.assertRaises(ConnectionError):
                await src.send_text("hoi", channel=0)
        asyncio.run(run())

    def test_periodic_replies_still_work_beside_requests(self) -> None:
        async def run():
            src, port = connected_source()
            src._queue_cmd(l.SEQ_NEIGHBORS, p.command(p.NEIGHBORS_GET, l.SEQ_NEIGHBORS))
            src._tick(0.0)
            src._handle(bytes([p.REPLY, l.SEQ_NEIGHBORS, 0, 1]) + struct.pack("<HbhII", 101, 8, -50, 1, 9))
            task = asyncio.create_task(src.set_name("Meshpoint"))
            await answer(src, port)
            await task
            self.assertEqual(src.net_name, "Meshpoint")
            self.assertEqual(src.snapshot()["neighbors"][0]["node"], 101)
        asyncio.run(run())


class FakeSource:
    def __init__(self, label="base", channels=None, name=None, error=None):
        self.label, self.channels, self.net_name, self.error = label, channels if channels is not None else {0: ""}, name, error
        self.calls = []

    def snapshot(self):
        return {"label": self.label}

    async def send_text(self, text, channel=None, member=None):
        if self.error:
            raise self.error
        self.calls.append((text, channel, member))
        return {"counter": 1, "airtime_ms": 100, "status": "sent"}

    async def set_name(self, name):
        self.net_name = name


class TestSendRoutes(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.audit = AuditLogWriter(log_path=Path(self.tmp.name) / "audit.jsonl")
        self.claims = SessionClaims("test-admin", "admin", 1)

    def send(self, src, **kw):
        routes.init_routes([src])
        return asyncio.run(routes.send(routes.SendRequest(**kw), _claims=self.claims, audit=self.audit))

    def assert_refused(self, src, status, **kw):
        with self.assertRaises(HTTPException) as ctx:
            self.send(src, **kw)
        self.assertEqual(ctx.exception.status_code, status)
        return ctx.exception.detail

    def test_sends_cleaned_text(self) -> None:
        src = FakeSource()
        self.assertEqual(self.send(src, channel=0, text="  hoi  ")["counter"], 1)
        self.send(src, to=4, text="dm")
        self.assertEqual(src.calls, [("hoi", 0, None), ("dm", None, 4)])

    def test_checks_before_anything_is_sent(self) -> None:
        src = FakeSource(name="Meshpoint")
        self.assert_refused(src, 400, channel=0, text="   ")
        self.assert_refused(src, 400, text="hoi")                          # no target
        self.assert_refused(src, 400, channel=0, to=4, text="hoi")         # two targets
        self.assert_refused(src, 400, channel=3, text="hoi")               # no key for channel 3
        self.assert_refused(src, 400, to=0, text="hoi")                    # the server is no member
        self.assert_refused(src, 400, to=p.REPEATER_MEMBER_BASE | 1, text="hoi")
        self.assert_refused(src, 400, to=4, text="x" * (p.DIRECT_TEXT_MAX + 1))
        detail = self.assert_refused(src, 400, channel=0, text="x" * (p.channel_text_max("Meshpoint") + 1))
        self.assertIn("at most 144", detail)                                # 155 - "Meshpoint: "
        self.assertEqual(src.calls, [])
        self.send(src, channel=0, text="x" * p.channel_text_max("Meshpoint"))   # exactly fits

    def test_receiver_errors_become_messages(self) -> None:
        for error, status in [(l.CommandError(p.ERR_STATE), 409), (l.CommandError(None), 504),
                              (l.CommandError(p.ERR_ARGUMENT), 400), (ConnectionError(), 503)]:
            with self.subTest(error=error):
                self.assert_refused(FakeSource(error=error), status, channel=0, text="hoi")

    def test_unknown_receiver(self) -> None:
        routes.init_routes([FakeSource("a"), FakeSource("b")])
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(routes.send(routes.SendRequest(receiver="c", channel=0, text="x"), _claims=self.claims, audit=self.audit))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_name_is_set_and_remembered(self) -> None:
        src = FakeSource()
        routes.init_routes([src])
        state.init({"devices": [{"serial_port": "/dev/a", "label": "base"}]})
        self.addCleanup(state.init, {})
        with mock.patch.object(state, "_persist") as persist:
            r = asyncio.run(routes.set_name(routes.NameRequest(name=" Meshpoint zolder "), _claims=self.claims, audit=self.audit))
        self.assertEqual(r, {"saved": True, "name": "Meshpoint zolder"})
        self.assertEqual(state.devices()[0]["name"], "Meshpoint zolder")
        persist.assert_called_once()
        with self.assertRaises(HTTPException):
            asyncio.run(routes.set_name(routes.NameRequest(name="x" * 33), _claims=self.claims, audit=self.audit))


if __name__ == "__main__":
    unittest.main()
