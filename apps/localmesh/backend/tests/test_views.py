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

    def test_the_receiver_itself(self) -> None:
        out = views.members([("base", data(own_member=3, net_name="Einstein Meshpoint", members={3: {"id": 3, "flags": 0}}))])
        self.assertEqual((out[0]["member"], out[0]["own"], out[0]["own_receiver"], out[0]["name"]), (3, True, "base", "Einstein Meshpoint"))

    def test_two_receivers_add_counts_and_keep_freshest(self) -> None:
        h = lambda count, t, rssi: {4: {"count": count, "last_heard": t, "type": "channel", "hops": 0, "via": 0,
                                        "rssi": rssi, "snr": 1.0}}
        out = views.members([("a", data(heard=h(2, 10.0, -90))), ("b", data(heard=h(3, 20.0, -60)))])
        self.assertEqual((out[0]["count"], out[0]["rssi"], out[0]["receivers"]), (5, -60, ["a", "b"]))

    def test_text_pin_sets_position(self) -> None:
        """A location shared as plain chat text ("Name: 📍 lat,lon") is treated the same as an advert position."""
        base = data(messages=[msg(5, 1, "Einstein Tag: 📍 52.37018,4.85858", channel=1, received=100.0, time=100.0)])
        out = views.members([("base", base)])
        self.assertEqual((out[0]["lat"], out[0]["lon"]), (52.37018, 4.85858))

    def test_text_pin_without_name_prefix(self) -> None:
        base = data(messages=[msg(5, 1, "📍 1.0,2.0", channel=p.CHANNEL_DIRECT, received=100.0, time=100.0)])
        out = views.members([("base", base)])
        self.assertEqual((out[0]["lat"], out[0]["lon"]), (1.0, 2.0))

    def test_text_pin_does_not_overwrite_a_newer_advert(self) -> None:
        base = data(
            adverts={5: {"member": 5, "name": "Einstein Tag", "lat": 10.0, "lon": 10.0, "hops": 1, "via": 0,
                         "rssi": -10, "heard": 200.0}},
            messages=[msg(5, 1, "📍 52.37018,4.85858", channel=1, received=100.0, time=100.0)],
        )
        out = views.members([("base", base)])
        self.assertEqual((out[0]["lat"], out[0]["lon"]), (10.0, 10.0))

    def test_text_pin_wins_over_an_older_advert(self) -> None:
        base = data(
            adverts={5: {"member": 5, "name": "Einstein Tag", "lat": 10.0, "lon": 10.0, "hops": 1, "via": 0,
                         "rssi": -10, "heard": 50.0}},
            messages=[msg(5, 1, "📍 52.37018,4.85858", channel=1, received=100.0, time=100.0)],
        )
        out = views.members([("base", base)])
        self.assertEqual((out[0]["lat"], out[0]["lon"]), (52.37018, 4.85858))

    def test_embedded_pin_emoji_is_not_mistaken_for_a_location(self) -> None:
        base = data(messages=[msg(6, 1, "look at this 📍 thing", channel=1)],
                    heard={6: {"count": 1, "last_heard": 100.0, "type": "channel", "hops": 1, "via": 0, "rssi": -1, "snr": 1.0}})
        out = views.members([("base", base)])
        self.assertIsNone(out[0]["lat"])


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

    def test_unnamed_channel_and_name_from_text(self) -> None:
        a = data(messages=[msg(4, 1, "Einstein: I am OK"), msg(5, 2, "no name here")], channels={0: ""},
                 heard={4: {"count": 1, "last_heard": 1.0, "type": "channel", "hops": 0, "via": 0, "rssi": -50, "snr": 1}})
        out = views.messages([("base", a)])
        self.assertEqual([(m["sender"], m["channel_name"]) for m in out], [("Einstein", "channel 0"), ("", "channel 0")])
        self.assertEqual(views.members([("base", a)])[0]["name"], "Einstein")

    def test_name_prefix_is_stripped_only_when_it_is_the_name(self) -> None:
        a = data(messages=[msg(4, 1, "Einstein: hoi"), msg(5, 2, "Note: no prefix here"),
                           msg(4, 3, "Einstein: dm keeps it", channel=p.CHANNEL_DIRECT)],
                 adverts={5: {"name": "Bob", "heard": 1.0}})
        texts = {m["counter"]: (m["sender"], m["text"]) for m in views.messages([("", a)])}
        self.assertEqual(texts[1], ("Einstein", "hoi"))
        self.assertEqual(texts[2], ("Bob", "Note: no prefix here"))            # "Note" is not Bob's name
        self.assertEqual(texts[3], ("Einstein", "Einstein: dm keeps it"))      # direct messages carry no prefix

    def test_sent_messages_use_the_receiver_name(self) -> None:
        sent = {"time": 5.0, "direct": True, "channel": p.CHANNEL_DIRECT, "to": 4, "text": "hoi", "counter": 9,
                "airtime_ms": 80, "status": "delivered"}
        a = data(sent=[sent], own_member=3, net_name="Einstein Meshpoint", adverts={4: {"name": "Einstein", "heard": 1.0}})
        m = views.messages([("base", a)])[0]
        self.assertEqual((m["sender"], m["channel_name"], m["outgoing"], m["status"]), ("Einstein Meshpoint", "direct to Einstein", True, "delivered"))
        a["net_name"] = None
        self.assertEqual(views.messages([("base", a)])[0]["sender"], "Meshpoint (base)")

    def test_advert_name_wins_over_text(self) -> None:
        a = data(messages=[msg(4, 1, "Bijnaam: hoi")], adverts={4: {"name": "Albert", "heard": 1.0}})
        self.assertEqual(views.messages([("", a)])[0]["sender"], "Albert")

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
