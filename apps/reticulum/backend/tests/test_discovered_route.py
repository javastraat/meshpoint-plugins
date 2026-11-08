"""GET /api/reticulum/discovered -- the Reticulum Dashboard's repeater layer.

FastAPI-gated (CI / Pi only).
"""

from __future__ import annotations

import unittest

try:
    import fastapi  # noqa: F401
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

_BACKBONE = {"name": "node.reticulumnet.nl", "type": "BackboneInterface", "latitude": 52.1,
             "longitude": 5.1, "status": "available", "last_heard": 1.0,
             "reachable_on": "node.reticulumnet.nl", "port": 4242}


@unittest.skipUnless(_HAS_FASTAPI, "routes imports fastapi (CI / Pi only)")
class TestDiscoveredRoute(unittest.TestCase):
    def setUp(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.api.auth.dependencies import optional_auth
        from src.api.auth.jwt_session import ROLE_ADMIN, ROLE_VIEWER, SessionClaims
        from plugins.apps.reticulum.backend import discovered, routes, state

        self._discovered, self._state = discovered, state
        self._orig_read = discovered.read_discovered
        discovered.read_discovered = lambda _cfg: [dict(_BACKBONE)]
        discovered.reset_cache()
        state.init({"discover_interfaces": True})

        self.claims = None
        app = FastAPI()
        app.dependency_overrides[optional_auth] = lambda: self.claims
        app.include_router(routes.router)
        self.client = TestClient(app)
        self.admin = SessionClaims(subject="a", role=ROLE_ADMIN, session_version=1)
        self.viewer = SessionClaims(subject="v", role=ROLE_VIEWER, session_version=1)

    def tearDown(self) -> None:
        self._discovered.read_discovered = self._orig_read
        self._discovered.reset_cache()
        self._state.init({})

    def _get(self) -> dict:
        r = self.client.get("/api/reticulum/discovered")
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_admin_gets_the_address(self) -> None:
        self.claims = self.admin
        (e,) = self._get()["interfaces"]
        self.assertEqual((e["reachable_on"], e["port"]), ("node.reticulumnet.nl", 4242))

    def test_viewer_and_anonymous_do_not(self) -> None:
        for claims in (self.viewer, None):
            self.claims = claims
            body = self._get()
            self.assertTrue(body["enabled"])
            self.assertNotIn("reachable_on", body["interfaces"][0])
            self.assertNotIn("port", body["interfaces"][0])

    def test_setting_off_is_empty(self) -> None:
        self._state.init({"discover_interfaces": False})
        self.claims = self.admin
        self.assertEqual(self._get(), {"enabled": False, "interfaces": []})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
