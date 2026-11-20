"""The running bot (backend/service.py) against a fake receiver and the shared registry: stdlib only."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
import types
import unittest
from pathlib import Path

_PLUGIN = Path(__file__).resolve().parents[2]
if "lmbot_backend" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("lmbot_backend", _PLUGIN / "backend" / "__init__.py",
                                                   submodule_search_locations=[str(_PLUGIN / "backend")])
    _pkg = importlib.util.module_from_spec(_spec)
    sys.modules["lmbot_backend"] = _pkg
    _spec.loader.exec_module(_pkg)

from lmbot_backend import service as sv  # noqa: E402


class FakeSource:
    def __init__(self, label: str = "base", name: str = "Einstein Meshpoint", fail: bool = False) -> None:
        self._label, self.net_name, self.fail = label, name, fail
        self.info = types.SimpleNamespace(member=15885631)
        self.repeaters = {42978: {"name": "NL-AMS-1057"}}
        self.channels = {0: "Algemeen", 3: "Test"}
        self.connected = True
        self.sent = []

    async def send_text(self, text: str, *, channel=None, member=None) -> dict:
        if self.fail:
            raise RuntimeError("airtime budget used up")
        self.sent.append((channel, text))
        return {}


def msg(text: str, channel: int = 3, member: int = 3277263) -> dict:
    t = time.time()
    return {"member": member, "channel": channel, "counter": 1, "time": t, "received": t, "hops": 3, "via": 42978,
            "rssi": -34, "snr": 12.0, "text": text, "path": [42978]}


class TestService(unittest.TestCase):
    def setUp(self) -> None:
        self.saved_registry = sys.modules.pop(sv.REGISTRY_MODULE, None)
        self.src = FakeSource()
        sv.registry().sources = (self.src,)
        self.saved = []
        self.service = sv.BotService({"enabled": True, "channels": [3]}, save=self.saved.append)
        self.service.attach()

    def tearDown(self) -> None:
        self.service.detach()
        sys.modules.pop(sv.REGISTRY_MODULE, None)
        if self.saved_registry is not None:
            sys.modules[sv.REGISTRY_MODULE] = self.saved_registry

    def deliver(self, m: dict, source=None) -> None:
        async def go() -> None:
            for h in list(sv.registry().message_handlers):   # what the localmesh plugin's hooks.emit_message does
                h(source or self.src, m)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        asyncio.run(go())

    def test_answers_through_the_same_receiver_on_the_same_channel(self) -> None:
        self.deliver(msg("Einstein Tag: .trace"))
        self.assertEqual(self.src.sent, [(3, "@Einstein Tag 1 hop: NL-AMS-1057 (42978), -34 dBm / 12 dB")])
        self.assertEqual(self.service.log[-1]["status"], "sent")

    def test_attach_once(self) -> None:
        self.service.attach()
        self.assertEqual(sv.registry().message_handlers.count(self.service.on_message), 1)

    def test_other_receiver_and_other_channel_are_ignored(self) -> None:
        self.deliver(msg(".ping"), source=FakeSource(label="attic"))
        self.deliver(msg(".ping", channel=0))
        self.assertEqual(self.src.sent, [])

    def test_a_failed_send_is_logged_and_the_bot_carries_on(self) -> None:
        self.src.fail = True
        self.deliver(msg(".ping"))
        self.assertTrue(self.service.log[-1]["status"].startswith("not sent: airtime budget"))

    def test_quiet_reasons_worth_seeing_are_logged(self) -> None:
        self.deliver(msg(".blah"))
        self.assertEqual(self.service.log[-1]["why"], "unknown command blah")
        n = len(self.service.log)
        self.deliver(msg("just chatting"))   # ordinary chat: not logged
        self.assertEqual(len(self.service.log), n)

    def test_set_config_saves_and_applies(self) -> None:
        self.service.set_config({"enabled": True, "channels": [0], "commands": {"weer": "zon"}})
        self.assertEqual(self.saved[-1]["channels"], [0])
        self.deliver(msg(".weer", channel=0))
        self.assertEqual(self.src.sent, [(0, "zon")])

    def test_receivers_for_the_tab(self) -> None:
        r = self.service.receivers()
        self.assertEqual(r, [{"label": "base", "name": "Einstein Meshpoint", "member": 15885631, "connected": True,
                              "channels": {"0": "Algemeen", "3": "Test"}}])

    def test_text_max_takes_the_name_off(self) -> None:
        self.assertEqual(sv.text_max(self.src), 151 - len("Einstein Meshpoint: "))


if __name__ == "__main__":
    unittest.main()
