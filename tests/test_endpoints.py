"""Tests for display REST API endpoints."""

import json
from types import SimpleNamespace
from unittest.mock import patch

from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application

import panel_live_server.endpoints as endpoints_module
from panel_live_server.endpoints import HealthEndpoint
from panel_live_server.endpoints import SnippetEndpoint


class _FakeDB:
    """Minimal fake DB for endpoint tests."""

    def __init__(self) -> None:
        self.method_seen: str | None = None
        self.raise_value_error: bool = False

    def create_visualization(self, app: str, name: str = "", description: str = "", method: str = "jupyter") -> SimpleNamespace:
        self.method_seen = method
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
            "method": "jupyter",
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
            "method": "jupyter",
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
            "method": "jupyter",
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
