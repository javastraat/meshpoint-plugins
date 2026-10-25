"""Route tests for backend/scrape_routes.py -- the Scrape-tab endpoints.

Needs FastAPI (CI / Pi only -- the dev Mac has no fastapi), same gate
test_nomad_routes.py already uses. The actual crawl/convert/write logic is
covered FastAPI-free by test_scraper.py; this only proves the routing,
auth tiers, and request validation. A fake Scraper stands in for the real
one so a ``/start`` call never touches the network from inside a test --
same idea as test_nomad_routes.py's ``_FakeLxmfService``.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

try:
    import fastapi  # noqa: F401
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

from plugins.apps.reticulum.backend import state


class _FakeScraper:
    """Duck-types backend/scraper.py's Scraper public surface, so route
    tests exercise routing/auth/validation without a real crawl."""

    def __init__(self):
        self.started_with = None
        self.stopped = False
        self.reloaded = False

    async def start(self, source_url, name, max_pages=None, max_depth=None):
        self.started_with = (source_url, name, max_pages, max_depth)
        return {"running": True, "name": name}

    async def stop(self):
        self.stopped = True
        return {"running": False}

    def status(self):
        return {"running": False, "name": "", "pages_done": 0, "log_lines": []}

    def reload_node(self):
        self.reloaded = True
        return True


@unittest.skipUnless(_HAS_FASTAPI, "scrape_routes imports fastapi (CI / Pi only)")
class TestScrapeRoutes(unittest.TestCase):
    def setUp(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.api.auth.dependencies import require_admin, require_auth
        from src.api.auth.jwt_session import ROLE_ADMIN, ROLE_VIEWER, SessionClaims

        from plugins.apps.reticulum.backend import scrape_routes

        self._tmp = tempfile.TemporaryDirectory()
        self.pages_dir = str(Path(self._tmp.name) / "pages")
        state.init({"node_enabled": True, "node_pages_dir": self.pages_dir})

        self.fake = _FakeScraper()
        scrape_routes.reset_routes()
        scrape_routes._scraper = self.fake  # bypass init_routes(service)

        app = FastAPI()
        app.dependency_overrides[require_admin] = lambda: SessionClaims(
            subject="admin", role=ROLE_ADMIN, session_version=1,
        )
        app.dependency_overrides[require_auth] = lambda: SessionClaims(
            subject="viewer", role=ROLE_VIEWER, session_version=1,
        )
        app.include_router(scrape_routes.router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        from plugins.apps.reticulum.backend import scrape_routes

        scrape_routes.reset_routes()
        state.init({})
        self._tmp.cleanup()

    def test_start_passes_fields_through(self) -> None:
        r = self.client.post("/api/reticulum/scrape/start", json={
            "source_url": "https://wiki.example.org/wiki/Main",
            "name": "hulp", "max_pages": 10, "max_depth": 2,
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            self.fake.started_with,
            ("https://wiki.example.org/wiki/Main", "hulp", 10, 2),
        )

    def test_start_missing_fields_is_422(self) -> None:
        r = self.client.post("/api/reticulum/scrape/start", json={"name": "hulp"})
        self.assertEqual(r.status_code, 422)

    def test_stop_calls_through(self) -> None:
        r = self.client.post("/api/reticulum/scrape/stop")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.fake.stopped)

    def test_status_is_readable(self) -> None:
        r = self.client.get("/api/reticulum/scrape/status")
        self.assertEqual(r.status_code, 200)
        self.assertIn("running", r.json())

    def test_collections_empty_initially(self) -> None:
        r = self.client.get("/api/reticulum/scrape/collections")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"collections": []})

    def test_collections_lists_a_scraped_folder(self) -> None:
        coll = Path(self.pages_dir) / "hulp"
        coll.mkdir(parents=True)
        (coll / "index.mu").write_text("hi", encoding="utf-8")
        (coll / "_meta.json").write_text(
            json.dumps({
                "name": "hulp", "source_url": "https://wiki.example.org/wiki/Main",
                "scraped_at": "2026-01-01", "pages": [{"slug": "index"}],
            }),
            encoding="utf-8",
        )
        r = self.client.get("/api/reticulum/scrape/collections")
        self.assertEqual(r.status_code, 200)
        names = [c["name"] for c in r.json()["collections"]]
        self.assertEqual(names, ["hulp"])

    def test_delete_removes_folder_and_reloads(self) -> None:
        coll = Path(self.pages_dir) / "hulp"
        coll.mkdir(parents=True)
        (coll / "index.mu").write_text("hi", encoding="utf-8")
        r = self.client.delete("/api/reticulum/scrape/collections/hulp")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(coll.exists())
        self.assertTrue(self.fake.reloaded)

    def test_delete_bad_name_is_400(self) -> None:
        # Not "../evil" -- the HTTP client itself normalises ".." out of the
        # URL before the request is even sent, so that never reaches the
        # route handler at all (path traversal is covered directly, at the
        # validator, by test_scraper.py's test_delete_rejects_path_traversal).
        # A validly-shaped-but-rejected single segment exercises the same
        # 400 path for real: the collection slug must be lowercase.
        r = self.client.delete("/api/reticulum/scrape/collections/UPPERCASE")
        self.assertEqual(r.status_code, 400)


async def _deny_forbidden():
    """Stand-in for require_admin as seen by a non-admin session."""
    from fastapi import HTTPException
    raise HTTPException(403, "admin required")


@unittest.skipUnless(_HAS_FASTAPI, "scrape_routes imports fastapi (CI / Pi only)")
class TestScrapeRoutesAuthTiers(unittest.TestCase):
    """Starting/stopping/deleting hits the open internet or disk -- admin
    only, same tier as offline-map's own /start /stop. Status/collections
    are read-only and open to any authenticated session."""

    def setUp(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.api.auth.dependencies import require_admin, require_auth
        from src.api.auth.jwt_session import ROLE_VIEWER, SessionClaims

        from plugins.apps.reticulum.backend import scrape_routes

        self._tmp = tempfile.TemporaryDirectory()
        state.init({"node_enabled": True, "node_pages_dir": str(Path(self._tmp.name) / "pages")})
        scrape_routes.reset_routes()
        scrape_routes._scraper = _FakeScraper()

        app = FastAPI()
        app.dependency_overrides[require_admin] = _deny_forbidden
        app.dependency_overrides[require_auth] = lambda: SessionClaims(
            subject="viewer", role=ROLE_VIEWER, session_version=1,
        )
        app.include_router(scrape_routes.router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        from plugins.apps.reticulum.backend import scrape_routes

        scrape_routes.reset_routes()
        state.init({})
        self._tmp.cleanup()

    def test_start_requires_admin(self) -> None:
        r = self.client.post("/api/reticulum/scrape/start", json={
            "source_url": "https://wiki.example.org/wiki/Main", "name": "hulp",
        })
        self.assertEqual(r.status_code, 403)

    def test_stop_requires_admin(self) -> None:
        self.assertEqual(self.client.post("/api/reticulum/scrape/stop").status_code, 403)

    def test_delete_requires_admin(self) -> None:
        self.assertEqual(
            self.client.delete("/api/reticulum/scrape/collections/hulp").status_code, 403,
        )

    def test_status_does_not_require_admin(self) -> None:
        self.assertEqual(self.client.get("/api/reticulum/scrape/status").status_code, 200)

    def test_collections_does_not_require_admin(self) -> None:
        self.assertEqual(self.client.get("/api/reticulum/scrape/collections").status_code, 200)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
