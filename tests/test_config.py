"""Unit tests for config._resolve_external_url()."""

import os
from unittest.mock import patch

import pytest

from panel_live_server.config import _resolve_external_url
from panel_live_server.config import get_config
from panel_live_server.config import reset_config

# Env vars that must be absent by default to keep tests isolated
_CLEAR_VARS = {
    "PANEL_LIVE_SERVER_EXTERNAL_URL": "",
    "JUPYTERHUB_HOST": "",
    "JUPYTERHUB_SERVICE_PREFIX": "",
    "CODESPACE_NAME": "",
    "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "",
}


@pytest.fixture(autouse=True)
def reset():
    """Reset config singleton before/after each test."""
    reset_config()
    yield
    reset_config()


class TestResolveExternalUrl:
    """Tests for _resolve_external_url()."""

    def test_empty_env_returns_empty(self) -> None:
        """No relevant env vars set → empty string (local fallback)."""
        with patch.dict(os.environ, _CLEAR_VARS, clear=False):
            assert _resolve_external_url(5077) == ""

    def test_explicit_override_takes_priority(self) -> None:
        """PANEL_LIVE_SERVER_EXTERNAL_URL wins over all other vars."""
        env = {
            **_CLEAR_VARS,
            "PANEL_LIVE_SERVER_EXTERNAL_URL": "https://explicit.example.com/proxy/5077",
            "JUPYTERHUB_HOST": "https://hub.example.com",
            "JUPYTERHUB_SERVICE_PREFIX": "/user/alice/",
            "CODESPACE_NAME": "my-codespace",
        }
        with patch.dict(os.environ, env, clear=False):
            assert _resolve_external_url(5077) == "https://explicit.example.com/proxy/5077"

    def test_explicit_override_trailing_slash_stripped(self) -> None:
        """Trailing slash is stripped from PANEL_LIVE_SERVER_EXTERNAL_URL."""
        env = {**_CLEAR_VARS, "PANEL_LIVE_SERVER_EXTERNAL_URL": "https://explicit.example.com/proxy/5077/"}
        with patch.dict(os.environ, env, clear=False):
            assert _resolve_external_url(5077) == "https://explicit.example.com/proxy/5077"

    def test_jupyterhub_host_and_prefix_builds_url(self) -> None:
        """JUPYTERHUB_HOST + JUPYTERHUB_SERVICE_PREFIX produce the expected proxy URL."""
        env = {
            **_CLEAR_VARS,
            "JUPYTERHUB_HOST": "https://hub.example.com",
            "JUPYTERHUB_SERVICE_PREFIX": "/user/alice/",
        }
        with patch.dict(os.environ, env, clear=False):
            assert _resolve_external_url(5077) == "https://hub.example.com/user/alice/proxy/5077"

    def test_jupyterhub_host_scheme_normalised(self) -> None:
        """JUPYTERHUB_HOST without a scheme gets https:// prepended."""
        env = {
            **_CLEAR_VARS,
            "JUPYTERHUB_HOST": "hub.example.com",
            "JUPYTERHUB_SERVICE_PREFIX": "/user/alice/",
        }
        with patch.dict(os.environ, env, clear=False):
            assert _resolve_external_url(5077) == "https://hub.example.com/user/alice/proxy/5077"

    def test_jupyterhub_host_trailing_slash_normalised(self) -> None:
        """Trailing slash on JUPYTERHUB_HOST is not doubled."""
        env = {
            **_CLEAR_VARS,
            "JUPYTERHUB_HOST": "https://hub.example.com/",
            "JUPYTERHUB_SERVICE_PREFIX": "/user/alice/",
        }
        with patch.dict(os.environ, env, clear=False):
            assert _resolve_external_url(5077) == "https://hub.example.com/user/alice/proxy/5077"

    def test_jupyterhub_host_only_falls_through(self) -> None:
        """Only JUPYTERHUB_HOST set (no prefix) → does not produce a partial URL."""
        env = {**_CLEAR_VARS, "JUPYTERHUB_HOST": "https://hub.example.com"}
        with patch.dict(os.environ, env, clear=False):
            assert _resolve_external_url(5077) == ""

    def test_jupyterhub_prefix_only_falls_through(self) -> None:
        """Only JUPYTERHUB_SERVICE_PREFIX set (no host) → does not produce a partial URL."""
        env = {**_CLEAR_VARS, "JUPYTERHUB_SERVICE_PREFIX": "/user/alice/"}
        with patch.dict(os.environ, env, clear=False):
            assert _resolve_external_url(5077) == ""

    def test_codespaces_url(self) -> None:
        """CODESPACE_NAME + GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN → correct URL."""
        env = {
            **_CLEAR_VARS,
            "CODESPACE_NAME": "literate-chainsaw-abc123",
            "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "app.github.dev",
        }
        with patch.dict(os.environ, env, clear=False):
            assert _resolve_external_url(5077) == "https://literate-chainsaw-abc123-5077.app.github.dev"

    def test_codespaces_default_domain(self) -> None:
        """CODESPACE_NAME alone uses the default forwarding domain."""
        env = {**_CLEAR_VARS, "CODESPACE_NAME": "my-space-xyz"}
        with patch.dict(os.environ, env, clear=False):
            assert _resolve_external_url(5077) == "https://my-space-xyz-5077.app.github.dev"

    def test_jupyterhub_takes_priority_over_codespaces(self) -> None:
        """JupyterHub vars take priority over Codespaces vars."""
        env = {
            **_CLEAR_VARS,
            "JUPYTERHUB_HOST": "https://hub.example.com",
            "JUPYTERHUB_SERVICE_PREFIX": "/user/alice/",
            "CODESPACE_NAME": "my-codespace",
        }
        with patch.dict(os.environ, env, clear=False):
            assert _resolve_external_url(5077) == "https://hub.example.com/user/alice/proxy/5077"

    def test_port_embedded_in_url(self) -> None:
        """The port number is correctly embedded in the URL."""
        env = {
            **_CLEAR_VARS,
            "JUPYTERHUB_HOST": "https://hub.example.com",
            "JUPYTERHUB_SERVICE_PREFIX": "/user/alice/",
        }
        with patch.dict(os.environ, env, clear=False):
            assert _resolve_external_url(9999) == "https://hub.example.com/user/alice/proxy/9999"


class TestGetConfigExternalUrl:
    """Tests that get_config() populates external_url correctly via _resolve_external_url."""

    def test_get_config_uses_jupyterhub_vars(self) -> None:
        """get_config().external_url reflects JupyterHub env vars."""
        env = {
            **_CLEAR_VARS,
            "JUPYTERHUB_HOST": "https://hub.example.com",
            "JUPYTERHUB_SERVICE_PREFIX": "/user/bob/",
            "PANEL_LIVE_SERVER_PORT": "5077",
        }
        with patch.dict(os.environ, env, clear=False):
            config = get_config()
            assert config.external_url == "https://hub.example.com/user/bob/proxy/5077"

    def test_get_config_empty_when_no_env(self) -> None:
        """get_config().external_url is empty string when no external env vars are set."""
        env = {**_CLEAR_VARS, "PANEL_LIVE_SERVER_PORT": "5077"}
        with patch.dict(os.environ, env, clear=False):
            config = get_config()
            assert config.external_url == ""
