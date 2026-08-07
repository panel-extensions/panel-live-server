"""Tests for display REST API endpoints."""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application

import panel_live_server.endpoints as endpoints_module
import panel_live_server.screenshot as screenshot_module
from panel_live_server.database import SnippetDatabase
from panel_live_server.endpoints import HealthEndpoint
from panel_live_server.endpoints import ScreenshotEndpoint
from panel_live_server.endpoints import SnippetEndpoint
from panel_live_server.validation import SecurityError


class _FakeDB:
    """Minimal fake DB for endpoint tests."""

    def __init__(self) -> None:
        self.method_seen: str | None = None
        self.skip_validation_seen: bool | None = None
        self.raise_value_error: bool = False

    def create_visualization(self, app: str, name: str = "", description: str = "", method: str = "inline", skip_validation: bool = False) -> SimpleNamespace:
        self.method_seen = method
        self.skip_validation_seen = skip_validation
        if self.raise_value_error:
            raise ValueError("Unsupported execution method 'invalid'. Supported methods: jupyter, panel, pyodide")
        return SimpleNamespace(id="snippet-123", error_message=None)


class TestSnippetEndpoint(AsyncHTTPTestCase):
    """Endpoint tests for /api/snippet."""

    def setUp(self) -> None:
        self.fake_db = _FakeDB()
        self._original_get_db = endpoints_module.get_db
        endpoints_module.get_db = lambda: self.fake_db
        super().setUp()

    def tearDown(self) -> None:
        endpoints_module.get_db = self._original_get_db
        super().tearDown()

    def get_app(self) -> Application:
        return Application(
            [
                (r"/api/snippet", SnippetEndpoint),
                (r"/api/health", HealthEndpoint),
            ]
        )

    def test_create_snippet_accepts_pyodide_method(self) -> None:
        """POST /api/snippet accepts pyodide and returns a URL payload."""
        body = {
            "code": "print('hello')",
            "name": "Pyodide test",
            "description": "Smoke test",
            "method": "pyodide",
        }

        response = self.fetch(
            "/api/snippet",
            method="POST",
            body=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )

        assert response.code == 200
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["id"] == "snippet-123"
        assert "view?id=snippet-123" in payload["url"]
        assert self.fake_db.method_seen == "pyodide"

    def test_create_snippet_invalid_method_returns_400(self) -> None:
        """POST /api/snippet maps ValueError to HTTP 400."""
        self.fake_db.raise_value_error = True

        body = {
            "code": "print('hello')",
            "method": "invalid",
        }

        response = self.fetch(
            "/api/snippet",
            method="POST",
            body=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )

        assert response.code == 400
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["error"] == "ValueError"
        assert "Unsupported execution method" in payload["message"]

    def test_create_snippet_uses_codespaces_url_when_available(self) -> None:
        """POST /api/snippet should return Codespaces-forwarded URL when config.external_url is set."""
        body = {
            "code": "print('hello')",
            "method": "inline",
        }

        fake_config = SimpleNamespace(external_url="https://literate-chainsaw-54wjwvrrxv4c4p5q-5077.app.github.dev")

        with patch.object(endpoints_module, "get_config", return_value=fake_config):
            response = self.fetch(
                "/api/snippet",
                method="POST",
                body=json.dumps(body),
                headers={"Content-Type": "application/json"},
            )

        assert response.code == 200
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["url"] == "https://literate-chainsaw-54wjwvrrxv4c4p5q-5077.app.github.dev/view?id=snippet-123"

    def test_create_snippet_uses_jupyter_proxy_external_url(self) -> None:
        """POST /api/snippet should use external_url from config when set to a Jupyter proxy URL."""
        body = {
            "code": "print('hello')",
            "method": "inline",
        }

        fake_config = SimpleNamespace(external_url="https://proxy.example.dev/user/foo/proxy/5077")

        with patch.object(endpoints_module, "get_config", return_value=fake_config):
            response = self.fetch(
                "/api/snippet",
                method="POST",
                body=json.dumps(body),
                headers={"Content-Type": "application/json"},
            )

        assert response.code == 200
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["url"] == "https://proxy.example.dev/user/foo/proxy/5077/view?id=snippet-123"

    def test_create_snippet_uses_configured_external_url(self) -> None:
        """config.external_url should be used for URL construction when set."""
        body = {
            "code": "print('hello')",
            "method": "inline",
        }

        fake_config = SimpleNamespace(external_url="https://config-proxy.example.dev/user/proxy/5077")

        with patch.object(endpoints_module, "get_config", return_value=fake_config):
            response = self.fetch(
                "/api/snippet",
                method="POST",
                body=json.dumps(body),
                headers={"Content-Type": "application/json"},
            )

        assert response.code == 200
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["url"] == "https://config-proxy.example.dev/user/proxy/5077/view?id=snippet-123"


class _FakeDraftDB:
    """Fake DB recording the create/delete pair a draft screenshot performs."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.deleted: list[str] = []
        self.error_message: str | None = None
        self.raise_exc: Exception | None = None

    def create_visualization(self, app: str, name: str = "", description: str = "", method: str = "inline", skip_validation: bool = False) -> SimpleNamespace:
        self.created.append(app)
        if self.raise_exc:
            raise self.raise_exc
        return SimpleNamespace(id="draft-1", error_message=self.error_message)

    def delete_snippet(self, snippet_id: str) -> bool:
        self.deleted.append(snippet_id)
        return True

    def get_snippet(self, snippet_id: str) -> None:
        return None


class TestScreenshotEndpointDrafts(AsyncHTTPTestCase):
    """POST /api/screenshot renders code that was never shown, then discards it (issue #43)."""

    def setUp(self) -> None:
        self.fake_db = _FakeDraftDB()
        self._original_get_db = endpoints_module.get_db
        endpoints_module.get_db = lambda: self.fake_db
        super().setUp()

    def tearDown(self) -> None:
        endpoints_module.get_db = self._original_get_db
        super().tearDown()

    def get_app(self) -> Application:
        return Application([(r"/api/screenshot", ScreenshotEndpoint)])

    def _post(self, body: dict) -> object:
        return self.fetch(
            "/api/screenshot",
            method="POST",
            body=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )

    def test_draft_is_captured_then_deleted(self) -> None:
        """A posted snippet is rendered and removed, so it never lands in the feed."""
        captured: dict[str, str] = {}

        async def fake_capture(url, **kwargs):
            captured["url"] = url
            return screenshot_module.Capture(pages=[("", b"\x89PNG-bytes")])

        with patch("panel_live_server.screenshot.capture_pages", fake_capture):
            response = self._post({"code": "1 + 1", "method": "inline"})

        assert response.code == 200
        assert response.headers["Content-Type"] == "image/png"
        assert response.body == b"\x89PNG-bytes"
        assert "view?id=draft-1" in captured["url"]
        assert self.fake_db.created == ["1 + 1"]
        assert self.fake_db.deleted == ["draft-1"]

    def test_draft_is_deleted_even_when_capture_fails(self) -> None:
        """A capture that blows up must not strand the draft in the database."""

        async def boom(url, **kwargs):
            raise RuntimeError("browser exploded")

        with patch("panel_live_server.screenshot.capture_pages", boom):
            response = self._post({"code": "1 + 1"})

        assert response.code == 500
        assert self.fake_db.deleted == ["draft-1"]

    def test_runtime_error_returns_400_and_deletes_the_draft(self) -> None:
        """Code that raised during validation comes back as a message, not a picture."""
        self.fake_db.error_message = "NameError: name 'nope' is not defined"

        response = self._post({"code": "nope"})

        assert response.code == 400
        payload = json.loads(response.body.decode("utf-8"))
        assert "NameError" in payload["message"]
        assert self.fake_db.deleted == ["draft-1"]

    def test_security_error_returns_400(self) -> None:
        """A blocked import is reported before anything is rendered."""
        self.fake_db.raise_exc = SecurityError("pickle is not allowed")

        response = self._post({"code": "import pickle"})

        assert response.code == 400
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["error"] == "SecurityError"
        assert self.fake_db.deleted == []

    def test_missing_code_returns_400(self) -> None:
        response = self._post({"method": "inline"})

        assert response.code == 400
        assert self.fake_db.created == []

    def test_get_with_unknown_id_still_404s(self) -> None:
        """The shared-capture refactor must not lose the existing id check."""
        response = self.fetch("/api/screenshot?id=nope")

        assert response.code == 404


class TestScreenshotDraftLeavesNoTrace(AsyncHTTPTestCase):
    """The real database must be back to where it started once a draft is captured."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = SnippetDatabase(Path(self._tmp.name) / "drafts.db")
        self._original_get_db = endpoints_module.get_db
        endpoints_module.get_db = lambda: self.db
        super().setUp()

    def tearDown(self) -> None:
        endpoints_module.get_db = self._original_get_db
        super().tearDown()
        self._tmp.cleanup()

    def get_app(self) -> Application:
        return Application([(r"/api/screenshot", ScreenshotEndpoint)])

    def test_draft_never_reaches_the_feed(self) -> None:
        """This is the whole point of issue #43: reviewing a draft must stay invisible."""
        seen_ids = []

        async def fake_capture(url, **kwargs):
            # Mid-capture the row must exist — the browser has to have a page to load.
            seen_ids.append([s.id for s in self.db.list_snippets()])
            return screenshot_module.Capture(pages=[("", b"\x89PNG")])

        assert self.db.list_snippets() == []

        with patch("panel_live_server.screenshot.capture_pages", fake_capture):
            response = self.fetch(
                "/api/screenshot",
                method="POST",
                body=json.dumps({"code": "1 + 1", "name": "Draft", "method": "inline"}),
                headers={"Content-Type": "application/json"},
            )

        assert response.code == 200
        assert len(seen_ids[0]) == 1, "the draft must exist while the browser loads it"
        assert self.db.list_snippets() == [], "the draft must be gone afterwards"
