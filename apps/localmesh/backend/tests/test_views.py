"""Tests for views.py: the Members, Messages and Activity lists merged over receivers."""

from __future__ import annotations

import unittest

from plugins.apps.localmesh.backend import protocol as p
from plugins.apps.localmesh.backend import views


def data(**kw) -> dict:
    d = {"messages": [], "activity": [], "adverts": {}, "heard": {}, "members": {}, "channels": {}}
    d.update(kw)
    return d


def msg(member: int, counter: int, text: str, channel: int = 0, received: float = 100.0, time=None) -> dict:
    return {"index": 0, "channel": channel, "member": member, "counter": counter, "time": time, "hops": 1, "via": 100,
            "rssi": -70, "snr": 2.0, "text": text, "received": received}


class TestMembers(unittest.TestCase):
    def test_merge_list_adverts_and_heard(self) -> None:
        base = data(
            members={4: {"id": 4, "flags": 0, "blocked": False}, 9: {"id": 9, "flags": p.MEMBER_BLOCKED, "blocked": True}},
            adverts={4: {"member": 4, "name": "Albert", "lat": 52.1, "lon": 4.9, "hops": 2, "via": 100, "rssi": -80,
                         "heard": 50.0}},
            heard={4: {"count": 3, "last_heard": 60.0, "type": "channel", "hops": 1, "via": 100, "rssi": -75, "snr": 4.0},
                   0: {"count": 1, "last_heard": 61.0, "type": "admin", "hops": 0, "via": 0, "rssi": -1, "snr": 0},
                   p.REPEATER_MEMBER_BASE | 100: {"count": 5, "last_heard": 62.0, "type": "status", "hops": 0,
                                                  "via": 0, "rssi": -50, "snr": 9}})
        out = views.members([("base", base)])
        self.assertEqual([m["member"] for m in out], [4, 9])   # heard first; no server, no repeater
        m4 = out[0]
        self.assertEqual((m4["name"], m4["lat"], m4["count"], m4["last_heard"], m4["hops"], m4["type"]),
                         ("Albert", 52.1, 3, 60.0, 1, "channel"))
        self.assertTrue(m4["listed"]); self.assertEqual(m4["receivers"], ["base"])
        self.assertTrue(out[1]["blocked"]); self.assertIsNone(out[1]["last_heard"])

    def test_two_receivers_add_counts_and_keep_freshest(self) -> None:
        h = lambda count, t, rssi: {4: {"count": count, "last_heard": t, "type": "channel", "hops": 0, "via": 0,
                                        "rssi": rssi, "snr": 1.0}}
        out = views.members([("a", data(heard=h(2, 10.0, -90))), ("b", data(heard=h(3, 20.0, -60)))])
        self.assertEqual((out[0]["count"], out[0]["rssi"], out[0]["receivers"]), (5, -60, ["a", "b"]))


class TestMessages(unittest.TestCase):
    def test_names_channels_order_and_dedup(self) -> None:
        a = data(messages=[msg(4, 1, "eerste", received=10.0), msg(5, 2, "dm", channel=p.CHANNEL_DIRECT, received=30.0)],
                 channels={0: "Algemeen"}, adverts={4: {"name": "Albert", "heard": 1.0}})
        b = data(messages=[msg(4, 1, "eerste", received=11.0), msg(4, 3, "klok", channel=7, received=5.0, time=20.0)])
        out = views.messages([("base", a), ("attic", b)])
        self.assertEqual([m["text"] for m in out], ["dm", "klok", "eerste"])
        self.assertEqual(out[0]["channel_name"], "direct to base"); self.assertTrue(out[0]["direct"])
        self.assertEqual(out[1]["channel_name"], "channel 7")        # attic has no name for it
        self.assertEqual((out[2]["channel_name"], out[2]["sender"]), ("Algemeen", "Albert"))

    def test_limit(self) -> None:
        a = data(messages=[msg(4, i, str(i), received=float(i)) for i in range(10)])
        self.assertEqual([m["text"] for m in views.messages([("", a)], limit=3)], ["9", "8", "7"])


class TestActivity(unittest.TestCase):
    def test_newest_first_with_receiver_and_name(self) -> None:
        """A name from any receiver's advert is used for every receiver's rows."""
        row = lambda t, member: {"member": member, "time": t, "sender": str(member), "type": "channel"}
        out = views.activity([("a", data(activity=[row(1.0, 4), row(3.0, 5)], adverts={4: {"name": "Albert", "heard": 0}})),
                              ("b", data(activity=[row(2.0, 4)]))])
        self.assertEqual([(r["time"], r["receiver"], r["name"]) for r in out], [(3.0, "a", ""), (2.0, "b", "Albert"), (1.0, "a", "Albert")])


if __name__ == "__main__":
    unittest.main()
