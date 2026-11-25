"""/api/localmesh-repeaters/*: who may do what. Needs FastAPI and Meshpoint's src/ on the path (skipped otherwise)."""

from __future__ import annotations

import sys
import unittest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api.auth.dependencies import require_admin, require_auth
    from src.api.auth.jwt_session import ROLE_ADMIN, SessionClaims
    _READY = True
except ImportError:
    _READY = False

from . import test_service  # noqa: F401 -- loads the backend as lmr_backend


@unittest.skipUnless(_READY, "needs FastAPI and Meshpoint")
class TestRoutes(unittest.TestCase):
    def setUp(self) -> None:
        import importlib
        self.routes = importlib.import_module("lmr_backend.routes")

        class Service:
            config = {}
            def overview(self): return {"repeaters": []}
            async def set_config(self, cfg): self.config = cfg
        self.service = Service()
        self.routes.init_routes(self.service)
        self.app = FastAPI(); self.app.include_router(self.routes.router)
        self.client = TestClient(self.app)
        self.claims = lambda: SessionClaims(subject="x", role=ROLE_ADMIN, session_version=1)

    def tearDown(self) -> None:
        self.routes.init_routes(None)

    def test_needs_a_login_and_an_admin(self) -> None:
        self.assertGreaterEqual(self.client.get("/api/localmesh-repeaters/repeaters").status_code, 400)
        self.app.dependency_overrides[require_auth] = self.claims
        self.assertEqual(self.client.get("/api/localmesh-repeaters/repeaters").json(), {"repeaters": []})
        self.assertGreaterEqual(self.client.put("/api/localmesh-repeaters/settings", json={}).status_code, 400)

    def test_settings(self) -> None:
        self.app.dependency_overrides[require_admin] = self.claims
        put = lambda body: self.client.put("/api/localmesh-repeaters/settings", json=body).status_code
        self.assertEqual(put({"keep_days": 60, "hidden": [5]}), 200)
        self.assertEqual(self.service.config, {"keep_days": 60, "hidden": [5]})
        self.assertEqual((put({"keep_days": 0}), put({"keep_days": 400})), (422, 422))

    def test_not_running(self) -> None:
        self.routes.init_routes(None)
        self.app.dependency_overrides[require_auth] = self.claims
        self.assertEqual(self.client.get("/api/localmesh-repeaters/repeaters").status_code, 503)


if __name__ == "__main__":
    unittest.main()
