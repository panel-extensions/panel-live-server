"""Tests for Panel server subprocess management."""

import os
import sys
from types import SimpleNamespace

import pytest

import panel_live_server.manager as manager_module
from panel_live_server.manager import PanelServerManager
from panel_live_server.manager import _force_kill_pid

# ---------------------------------------------------------------------------
# Shared fake-psutil helpers
# ---------------------------------------------------------------------------


class FakeNoSuchProcess(Exception):
    pass


class FakeAccessDenied(Exception):
    pass


class FakeZombieProcess(FakeNoSuchProcess):
    pass


def _make_fake_psutil(*, kill_side_effect=None):
    """Build a fake psutil namespace with a configurable Process.kill()."""

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def kill(self):
            if kill_side_effect is not None:
                raise kill_side_effect(self.pid)

    return SimpleNamespace(
        Process=FakeProcess,
        NoSuchProcess=FakeNoSuchProcess,
        AccessDenied=FakeAccessDenied,
        ZombieProcess=FakeZombieProcess,
    )


@pytest.fixture()
def _patch_psutil(monkeypatch):
    """Patch the top-level psutil import in manager_module for tests."""

    def _patch(*, kill_side_effect=None):
        fake = _make_fake_psutil(kill_side_effect=kill_side_effect)
        monkeypatch.setattr(manager_module, "psutil", fake)
        return fake

    return _patch


# ---------------------------------------------------------------------------
# _force_kill_pid
# ---------------------------------------------------------------------------


class TestForceKillPid:
    def test_returns_true_on_success(self, _patch_psutil):
        _patch_psutil()
        assert _force_kill_pid(1234) is True

    def test_returns_true_on_no_such_process(self, _patch_psutil):
        _patch_psutil(kill_side_effect=FakeNoSuchProcess)
        assert _force_kill_pid(1234) is True

    def test_returns_true_on_zombie(self, _patch_psutil):
        _patch_psutil(kill_side_effect=FakeZombieProcess)
        assert _force_kill_pid(1234) is True

    def test_returns_false_on_access_denied(self, _patch_psutil):
        _patch_psutil(kill_side_effect=FakeAccessDenied)
        assert _force_kill_pid(1234) is False


# ---------------------------------------------------------------------------
# PanelServerManager._build_subprocess_env
# ---------------------------------------------------------------------------


def test_build_subprocess_env_prepends_environment_paths(monkeypatch, tmp_path):
    """Subprocess env should include environment DLL paths before PATH."""
    env_root = tmp_path / "env"
    scripts_dir = env_root / "Scripts"
    library_bin_dir = env_root / "Library" / "bin"
    dlls_dir = env_root / "DLLs"
    for path in (scripts_dir, library_bin_dir, dlls_dir):
        path.mkdir(parents=True)

    python_exe = env_root / "python.exe"
    python_exe.write_text("", encoding="utf-8")

    monkeypatch.setattr(sys, "executable", str(python_exe))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", "existing-path")

    db_path = tmp_path / "snippets.db"
    manager = PanelServerManager(db_path=db_path, port=5090, host="127.0.0.1")

    env = manager._build_subprocess_env()

    assert env["PANEL_LIVE_SERVER_DB_PATH"] == str(db_path)
    assert env["PANEL_LIVE_SERVER_PORT"] == "5090"
    assert env["PANEL_LIVE_SERVER_HOST"] == "127.0.0.1"

    path_entries = env["PATH"].split(os.pathsep)
    assert path_entries[:4] == [
        str(env_root),
        str(scripts_dir),
        str(library_bin_dir),
        str(dlls_dir),
    ]
    assert path_entries[-1] == "existing-path"


# ---------------------------------------------------------------------------
# PanelServerManager._try_recover_stale_server
# ---------------------------------------------------------------------------


def _health_response(prefix):
    """Build a fake healthy /api/health response carrying an interpreter prefix."""
    return SimpleNamespace(status_code=200, json=lambda: {"status": "healthy", "prefix": prefix})


def test_try_recover_adopts_healthy_unowned_server(monkeypatch, tmp_path):
    """A healthy same-environment server we don't own is adopted, not killed.

    Multiple MCP instances (e.g. the desktop UI client and the Cowork agent client)
    share one Panel server: only one can bind the port, so every other instance must
    adopt the running server instead of killing it and racing for the port.
    """
    manager = PanelServerManager(db_path=tmp_path / "snippets.db", port=5090, host="127.0.0.1")
    assert manager.process is None  # we did not start this server

    monkeypatch.setattr(manager_module.requests, "get", lambda *a, **kw: _health_response(sys.prefix))

    killed = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))

    assert manager._try_recover_stale_server() is True
    assert killed == []  # adopted, not killed


def test_try_recover_refuses_different_environment_server(monkeypatch, tmp_path):
    """A healthy server from a *different* interpreter must not be adopted.

    Adopting it would execute snippets against that environment's installed
    packages instead of the ones alongside this ``pls`` (issue #41).
    """
    manager = PanelServerManager(db_path=tmp_path / "snippets.db", port=5090, host="127.0.0.1")
    assert manager.process is None

    monkeypatch.setattr(manager_module.requests, "get", lambda *a, **kw: _health_response("/some/other/env"))

    killed = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))

    assert manager._try_recover_stale_server() is False
    assert killed == []  # not our env, but not force-killed either


def test_try_recover_adopts_owned_running_server(monkeypatch, tmp_path):
    """A healthy server we started ourselves is also reported as available."""
    manager = PanelServerManager(db_path=tmp_path / "snippets.db", port=5090, host="127.0.0.1")
    manager.process = SimpleNamespace(poll=lambda: None)  # we own a live subprocess

    monkeypatch.setattr(manager_module.requests, "get", lambda *a, **kw: SimpleNamespace(status_code=200))

    killed = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))

    assert manager._try_recover_stale_server() is True
    assert killed == []


def test_try_recover_unhealthy_handles_access_denied(monkeypatch, tmp_path, _patch_psutil):
    """AccessDenied during force-kill of stale process should not raise."""
    _patch_psutil(kill_side_effect=FakeAccessDenied)

    manager = PanelServerManager(db_path=tmp_path / "snippets.db", port=5090, host="127.0.0.1")

    def _raise_request_exception(*args, **kwargs):
        raise manager_module.requests.RequestException("unhealthy")

    monkeypatch.setattr(manager_module.requests, "get", _raise_request_exception)
    monkeypatch.setattr(manager, "_find_pid_on_port", lambda: 9876)
    monkeypatch.setattr(manager, "_is_port_in_use", lambda: True)
    monkeypatch.setattr(manager_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    assert manager._try_recover_stale_server() is False
