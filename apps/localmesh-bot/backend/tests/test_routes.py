"""GET/PUT /api/localmesh-bot/config and GET /log: the handlers called directly (as the localmesh plugin's route tests),
with a real audit writer in a temporary folder. Needs FastAPI and Meshpoint's src on the path."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

_PLUGIN = Path(__file__).resolve().parents[2]
if "lmbot_backend" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("lmbot_backend", _PLUGIN / "backend" / "__init__.py",
                                                   submodule_search_locations=[str(_PLUGIN / "backend")])
    _pkg = importlib.util.module_from_spec(_spec)
    sys.modules["lmbot_backend"] = _pkg
    _spec.loader.exec_module(_pkg)

from lmbot_backend import routes  # noqa: E402
from lmbot_backend.service import BotService  # noqa: E402
from src.api.audit import AuditLogWriter  # noqa: E402
from src.api.auth.jwt_session import SessionClaims  # noqa: E402


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestRoutes(unittest.TestCase):
    def setUp(self) -> None:
        self.saved = []
        self.service = BotService({"channels": [3]}, save=self.saved.append)
        routes.init_routes(self.service)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.audit = AuditLogWriter(log_path=Path(self.tmp.name) / "audit.jsonl")
        self.claims = SessionClaims("test-admin", "admin", 1)

    def test_get(self) -> None:
        got = _run(routes.get_config(_claims=self.claims))
        self.assertEqual(got["config"]["channels"], [3])
        self.assertEqual(got["builtin"], ["help", "ping", "trace"])
        self.assertIn("receivers", got)

    def test_put_saves(self) -> None:
        body = {"enabled": True, "channels": [0, 3], "prefix": "!", "commands": {"weer": "zon"}}
        got = _run(routes.put_config(body, _claims=self.claims, audit=self.audit))
        self.assertEqual(got["config"]["prefix"], "!")
        self.assertEqual(self.saved[-1]["commands"], {"weer": "zon"})
        self.assertIn("localmesh-bot.config_update", (Path(self.tmp.name) / "audit.jsonl").read_text())

    def test_put_refuses_with_what_is_wrong(self) -> None:
        with self.assertRaises(HTTPException) as cm:
            _run(routes.put_config({"channels": [3], "commands": {"ping": "x"}}, _claims=self.claims, audit=self.audit))
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("built in already", cm.exception.detail)
        self.assertEqual(self.saved, [])

    def test_log(self) -> None:
        self.service.log.append({"why": ".ping"})
        self.assertEqual(_run(routes.get_log(_claims=self.claims)), [{"why": ".ping"}])


if __name__ == "__main__":
    unittest.main()
