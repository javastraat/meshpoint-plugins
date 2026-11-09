"""/api/reticulum-repeaters/* -- access rules and validation.

FastAPI-gated (CI / Pi, or python3.11 on the dev Mac with meshpoint's src/
on the path as ``src`` and this backend importable as
``plugins.apps.reticulum_repeaters.backend``).
"""

from __future__ import annotations

import asyncio
import unittest

try:
    import fastapi  # noqa: F401
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

P1 = "c5861341a55adf62b385af734bf87c4a"


@unittest.skipUnless(_HAS_FASTAPI, "routes imports fastapi (CI / Pi only)")
class RoutesTest(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.api.auth.dependencies import require_admin, require_auth
        from src.api.auth.jwt_session import ROLE_ADMIN, ROLE_VIEWER, SessionClaims
        from plugins.apps.reticulum_repeaters.backend import routes, state

        self.state, self.routes = state, routes
        self._persist = state._persist
        state._persist = lambda: None            # never touch a real local.yaml
        state.init({"repeaters": [{"hash": P1, "name": "P1"}]})

        test = self

        class FakeMonitor:
            def __init__(self):
                self.latest = {P1: {"ok": True, "all": {"percent": 94}, "_nbrs_cols": ["x"]}}
                self.added, self.refreshed, self.forgot = [], [], []
                self.cooldown = 0

            def can_refresh(self, h):
                return test.mon.cooldown == 0, test.mon.cooldown

            def mark_refresh(self, h):
                pass

            async def refresh(self, h):
                self.refreshed.append(h)

            async def add(self, h):
                self.added.append(h)

            async def forget(self, h):
                self.forgot.append(h)

        self.mon = FakeMonitor()
        routes.init_routes(self.mon)
        self.role = ROLE_ADMIN

        def claims():
            return SessionClaims(subject="u", role=self.role, session_version=1)

        def admin_only():
            from fastapi import HTTPException
            if self.role != ROLE_ADMIN:
                raise HTTPException(403, "admin only")
            return claims()

        app = FastAPI()
        app.dependency_overrides[require_auth] = claims
        app.dependency_overrides[require_admin] = admin_only
        app.include_router(routes.router)
        self.client = TestClient(app)
        self.VIEWER = ROLE_VIEWER

    def tearDown(self):
        self.routes.reset_routes()
        self.state._persist = self._persist
        self.state.init({})

    def test_list_hides_internal_keys(self):
        body = self.client.get("/api/reticulum-repeaters/repeaters").json()
        snap = body["repeaters"][0]["snapshot"]
        self.assertEqual(snap["all"]["percent"], 94)
        self.assertNotIn("_nbrs_cols", snap)
        self.assertEqual(body["settings"]["interval_min"], 15)

    def test_list_hashes_collects_dest_and_via(self):
        snap = {"paths": {"cols": ["dest", "hops", "age_s", "via"],
                          "rows": [["DB5AEE50", 4, 6346, "6270bb29"], ["fac4f252", 1, 164, "37c51c69"]]},
                "nbrs": {"cols": ["dest", "age_s", "name"], "rows": [["3b9e9e51", 1248, None]]},
                "links": {"cols": ["link", "st", "via"], "rows": [["ab3c8908", "a", None]]}}
        self.assertEqual(self.routes._list_hashes(snap),
                         {"db5aee50", "6270bb29", "fac4f252", "37c51c69", "3b9e9e51"})

    def test_viewer_can_read_but_not_change(self):
        self.role = self.VIEWER
        self.assertEqual(self.client.get("/api/reticulum-repeaters/repeaters").status_code, 200)
        self.assertEqual(self.client.post(f"/api/reticulum-repeaters/repeaters/{P1}/refresh").status_code, 403)
        self.assertEqual(self.client.post("/api/reticulum-repeaters/repeaters",
                                          json={"hash": "ab" * 16}).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/reticulum-repeaters/repeaters/{P1}").status_code, 403)
        self.assertEqual(self.client.put("/api/reticulum-repeaters/settings", json={}).status_code, 403)

    def test_add_validates_and_rejects_duplicates(self):
        self.assertEqual(self.client.post("/api/reticulum-repeaters/repeaters",
                                          json={"hash": "zz" * 16}).status_code, 400)
        self.assertEqual(self.client.post("/api/reticulum-repeaters/repeaters",
                                          json={"hash": P1}).status_code, 409)
        r = self.client.post("/api/reticulum-repeaters/repeaters", json={"hash": "AB" * 16})
        self.assertEqual(r.status_code, 200)
        self.assertIn("ab" * 16, [x["hash"] for x in self.state.repeaters()])

    def test_refresh_cooldown_is_429(self):
        self.assertEqual(self.client.post(f"/api/reticulum-repeaters/repeaters/{P1}/refresh").status_code, 200)
        self.mon.cooldown = 42
        r = self.client.post(f"/api/reticulum-repeaters/repeaters/{P1}/refresh")
        self.assertEqual(r.status_code, 429)
        self.assertIn("42", r.json()["detail"])

    def test_refresh_unknown_repeater_is_404(self):
        self.assertEqual(self.client.post(f"/api/reticulum-repeaters/repeaters/{'ab' * 16}/refresh").status_code, 404)

    def test_remove(self):
        self.assertEqual(self.client.delete(f"/api/reticulum-repeaters/repeaters/{P1}").status_code, 200)
        self.assertEqual(self.mon.forgot, [P1])
        self.assertEqual(self.state.repeaters(), [])

    def test_settings_validation(self):
        self.assertEqual(self.client.put("/api/reticulum-repeaters/settings",
                                         json={"interval_min": 1}).status_code, 422)
        r = self.client.put("/api/reticulum-repeaters/settings",
                            json={"polling_enabled": False, "interval_min": 30, "lists_interval_min": 120})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self.state.polling_enabled())
        self.assertEqual(self.state.interval_s(), 1800)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
