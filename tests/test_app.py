"""Tests for app-level URL and websocket origin helpers."""

from types import SimpleNamespace

import panel_live_server.app as app_module


def test_build_websocket_origins_local_defaults(monkeypatch):
    """Local defaults should include localhost and loopback with port."""
    monkeypatch.setattr(app_module, "get_config", lambda: SimpleNamespace(external_url=""))

    origins = app_module._build_websocket_origins(address="localhost", port=5077)

    assert "localhost:5077" in origins
    assert "127.0.0.1:5077" in origins


def test_build_websocket_origins_includes_external_url_port(monkeypatch):
    """External URL with explicit port should be represented as host:port."""
    monkeypatch.setattr(
        app_module,
        "get_config",
        lambda: SimpleNamespace(external_url="https://demo.example.net:8443/proxy/5077"),
    )

    origins = app_module._build_websocket_origins(address="localhost", port=5077)

    assert "demo.example.net:8443" in origins


def test_build_websocket_origins_skips_wildcard_bind_address(monkeypatch):
    """Wildcard bind addresses should not be added directly as websocket origins."""
    monkeypatch.setattr(app_module, "get_config", lambda: SimpleNamespace(external_url=""))

    origins = app_module._build_websocket_origins(address="0.0.0.0", port=5077)

    assert "0.0.0.0:5077" not in origins
