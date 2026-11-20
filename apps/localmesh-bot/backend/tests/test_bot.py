"""Tests for the bot's decisions (backend/bot.py): pure Python, no receiver."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

# Load backend/ as a package under a test name (the folder has a hyphen), as reticulum-repeaters does.
_PLUGIN = Path(__file__).resolve().parents[2]
if "lmbot_backend" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("lmbot_backend", _PLUGIN / "backend" / "__init__.py",
                                                   submodule_search_locations=[str(_PLUGIN / "backend")])
    _pkg = importlib.util.module_from_spec(_spec)
    sys.modules["lmbot_backend"] = _pkg
    _spec.loader.exec_module(_pkg)

from lmbot_backend import bot  # noqa: E402


NOW = 1_795_200_000.0


def msg(text: str, member: int = 3277263, channel: int = 3, path=None, t: float = NOW - 2, rssi=-36, snr=11.75) -> dict:
    return {"member": member, "channel": channel, "counter": 1, "time": t, "received": t, "hops": 2, "via": 42978,
            "rssi": rssi, "snr": snr, "text": text, "path": list(path or [])}


NAMES = {42978: "NL-AMS-1057", 58159: "NL-AMS-1076"}


def ctx(now: float = NOW, own_name: str = "Einstein Meshpoint", text_max: int = 131) -> bot.Context:
    return bot.Context(own_member=15885631, own_name=own_name, now=now, text_max=text_max,
                       repeater_name=lambda n: f"{NAMES[n]} ({n})" if n in NAMES else str(n))


def cfg(**kw) -> bot.Config:
    return bot.Config.from_dict({"enabled": True, "channels": [3], **kw})


class TestCommands(unittest.TestCase):
    def setUp(self) -> None:
        self.lim = bot.Limiter()

    def test_ping_heard_directly_gives_the_askers_own_signal(self) -> None:
        reply, why = bot.decide(msg("Einstein Tag: .ping", rssi=-7, snr=12.0), cfg(), ctx(), self.lim)
        self.assertEqual(reply, "@Einstein Tag pong, heard directly, -7 dBm / 12 dB")
        self.assertEqual(why, ".ping")

    def test_ping_through_repeaters_says_the_signal_is_the_last_hop(self) -> None:
        """20-11: through a repeater the signal is how the bot heard that repeater, not the asker: say so."""
        reply, _ = bot.decide(msg(".ping", path=[58159, 42978]), cfg(), ctx(), self.lim)
        self.assertEqual(reply, "pong, 2 hops, last hop NL-AMS-1057 (42978) -36 dBm / 11.75 dB")

    def test_help_lists_builtin_and_own_commands(self) -> None:
        reply, _ = bot.decide(msg(".help"), cfg(commands={"weer": "Kijk naar buiten"}), ctx(), self.lim)
        self.assertEqual(reply, "Commands: .help .ping .trace .weer")

    def test_trace_names_the_repeaters_in_order(self) -> None:
        reply, _ = bot.decide(msg("Einstein Tag: .trace", path=[58159, 42978]), cfg(), ctx(), self.lim)
        self.assertEqual(reply, "@Einstein Tag 2 hops: NL-AMS-1076 (58159) → NL-AMS-1057 (42978), "
                                "last hop -36 dBm / 11.75 dB")

    def test_trace_heard_directly(self) -> None:
        reply, _ = bot.decide(msg(".trace", rssi=-7, snr=12.0), cfg(), ctx(), self.lim)
        self.assertEqual(reply, "heard directly, no repeater, -7 dBm / 12 dB")

    def test_own_command_and_case(self) -> None:
        reply, _ = bot.decide(msg(".WEER"), cfg(commands={"weer": "Kijk naar buiten"}), ctx(), self.lim)
        self.assertEqual(reply, "Kijk naar buiten")

    def test_other_prefix(self) -> None:
        self.assertEqual(bot.decide(msg("!ping"), cfg(prefix="!"), ctx(), self.lim)[0],
                         "pong, heard directly, -36 dBm / 11.75 dB")
        self.assertIsNone(bot.decide(msg(".ping"), cfg(prefix="!"), ctx(), bot.Limiter())[0])

    def test_reply_fits_the_receiver(self) -> None:
        long = "é" * 140
        reply, _ = bot.decide(msg(".x"), cfg(commands={"x": long}), ctx(text_max=21), self.lim)
        self.assertEqual(len(reply.encode()), 20)   # cut between characters, never inside one


class TestQuiet(unittest.TestCase):
    def quiet(self, m: dict, c: bot.Config = None, cx: bot.Context = None) -> str:
        reply, why = bot.decide(m, c or cfg(), cx or ctx(), bot.Limiter())
        self.assertIsNone(reply)
        return why

    def test_off(self) -> None:
        self.assertEqual(self.quiet(msg(".ping"), cfg(enabled=False)), "off")

    def test_other_channel_and_direct_messages(self) -> None:
        self.assertEqual(self.quiet(msg(".ping", channel=0)), "not a bot channel")
        self.assertEqual(self.quiet(msg(".ping", channel=255), cfg(channels=[3, 255])), "not a bot channel")

    def test_own_member_and_own_name(self) -> None:
        self.assertEqual(self.quiet(msg(".ping", member=15885631)), "own message")
        self.assertEqual(self.quiet(msg("Einstein Meshpoint: .ping", member=99)), "our own name in front")

    def test_old_message_from_the_buffer(self) -> None:
        self.assertEqual(self.quiet(msg(".ping", t=NOW - 600)), "too old")

    def test_no_command_and_unknown_command(self) -> None:
        self.assertEqual(self.quiet(msg("hallo allemaal")), "not a command")
        self.assertEqual(self.quiet(msg(".")), "not a command")
        self.assertEqual(self.quiet(msg(".blah")), "unknown command blah")


class TestLimits(unittest.TestCase):
    def test_cooldown_per_member(self) -> None:
        lim, c = bot.Limiter(), cfg(cooldown_s=20)
        self.assertIsNotNone(bot.decide(msg(".ping"), c, ctx(NOW), lim)[0])
        self.assertEqual(bot.decide(msg(".ping"), c, ctx(NOW + 5), lim), (None, "cooldown"))
        self.assertIsNotNone(bot.decide(msg(".ping", member=7), c, ctx(NOW + 5), lim)[0])   # someone else
        self.assertIsNotNone(bot.decide(msg(".ping", t=NOW + 19), c, ctx(NOW + 21), lim)[0])

    def test_hourly_limit_in_all(self) -> None:
        lim, c = bot.Limiter(), cfg(max_per_hour=2, cooldown_s=5)
        for i in range(2):
            self.assertIsNotNone(bot.decide(msg(".ping", member=i + 1), c, ctx(NOW + i), lim)[0])
        self.assertEqual(bot.decide(msg(".ping", member=9), c, ctx(NOW + 2), lim), (None, "hourly limit"))
        self.assertIsNotNone(bot.decide(msg(".ping", member=9, t=NOW + 3600), c, ctx(NOW + 3601), lim)[0])

    def test_an_unknown_command_costs_nothing(self) -> None:
        lim, c = bot.Limiter(), cfg(max_per_hour=1)
        bot.decide(msg(".blah"), c, ctx(), lim)
        self.assertIsNotNone(bot.decide(msg(".ping"), c, ctx(), lim)[0])


class TestAdmins(unittest.TestCase):
    """20-11: admin members are not held to the cooldown or the hourly limit -- only when a repeater passed the question
    on (a repeater checked the member key; heard directly, anyone with the channel key could use an admin's number)."""

    def test_admin_through_a_repeater_has_no_limits(self) -> None:
        lim, c = bot.Limiter(), cfg(admins=[3277263], max_per_hour=1, cooldown_s=60)
        for i in range(5):
            reply, why = bot.decide(msg(".ping", path=[42978]), c, ctx(NOW + i), lim)
            self.assertIsNotNone(reply, f"answer {i}")
            self.assertEqual(why, ".ping (admin)")

    def test_admin_answers_do_not_use_up_the_hour_for_others(self) -> None:
        lim, c = bot.Limiter(), cfg(admins=[3277263], max_per_hour=1)
        for i in range(3):
            bot.decide(msg(".ping", path=[42978]), c, ctx(NOW + i), lim)
        self.assertIsNotNone(bot.decide(msg(".ping", member=7), c, ctx(NOW + 5), lim)[0])

    def test_admin_heard_directly_keeps_the_limits(self) -> None:
        lim, c = bot.Limiter(), cfg(admins=[3277263], cooldown_s=60)
        self.assertEqual(bot.decide(msg(".ping"), c, ctx(NOW), lim)[1], ".ping")
        self.assertEqual(bot.decide(msg(".ping"), c, ctx(NOW + 1), lim), (None, "cooldown"))

    def test_others_keep_the_limits(self) -> None:
        lim, c = bot.Limiter(), cfg(admins=[3277263], cooldown_s=60)
        bot.decide(msg(".ping", member=7, path=[42978]), c, ctx(NOW), lim)
        self.assertEqual(bot.decide(msg(".ping", member=7, path=[42978]), c, ctx(NOW + 1), lim), (None, "cooldown"))

    def test_admin_list_cleaned_and_validated(self) -> None:
        self.assertEqual(bot.Config.from_dict({"admins": [5, 5, 0, -1, 2**24, 3277263]}).admins, [5, 3277263])
        self.assertEqual(bot.validate({"admins": [3277263]}), [])
        self.assertEqual(len(bot.validate({"admins": [0, "x"]})), 2)


class TestConfig(unittest.TestCase):
    def test_round_trip_and_cleaning(self) -> None:
        c = bot.Config.from_dict({"enabled": True, "channels": [3, 3, 0, 255, 300], "prefix": "a",
                                  "commands": {"Weer": "zon", "ping": "x", "bad name": "y", "leeg": " "},
                                  "cooldown_s": 1, "max_per_hour": 999})
        self.assertEqual(c.channels, [0, 3])
        self.assertEqual(c.prefix, ".")                       # a letter is no prefix
        self.assertEqual(c.commands, {"weer": "zon"})         # built in, bad and empty ones dropped
        self.assertEqual((c.cooldown_s, c.max_per_hour), (5, 120))
        self.assertEqual(bot.Config.from_dict(c.to_dict()), c)

    def test_validate_says_what_is_wrong(self) -> None:
        self.assertEqual(bot.validate({"channels": [3], "prefix": "!", "commands": {"weer": "zon"}}), [])
        errors = bot.validate({"channels": [255], "prefix": "ab", "commands": {"ping": "x", "a b": "y", "z": ""}})
        self.assertEqual(len(errors), 5, errors)


if __name__ == "__main__":
    unittest.main()
