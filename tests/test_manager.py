"""Tests for Panel server subprocess management."""

import os
import sys
from types import SimpleNamespace

import panel_live_server.manager as manager_module
from panel_live_server.manager import PanelServerManager


def test_build_subprocess_env_prepends_environment_paths(monkeypatch, tmp_path):
    """Subprocess env should include environment DLL paths before PATH."""
    import sys

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

    path_entries = env["PATH"].split(manager_module.os.pathsep)
    assert path_entries[:4] == [
        str(env_root),
        str(scripts_dir),
        str(library_bin_dir),
        str(dlls_dir),
    ]
    assert path_entries[-1] == "existing-path"


def test_try_recover_stale_orphan_handles_psutil_no_such_process(monkeypatch, tmp_path):
    """NoSuchProcess during force-kill should be treated as already gone."""

    class FakeNoSuchProcess(Exception):
        pass

    class FakeAccessDenied(Exception):
        pass

    class FakeZombieProcess(Exception):
        pass

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def kill(self):
            raise FakeNoSuchProcess(self.pid)

    fake_psutil = SimpleNamespace(
        Process=FakeProcess,
        NoSuchProcess=FakeNoSuchProcess,
        AccessDenied=FakeAccessDenied,
        ZombieProcess=FakeZombieProcess,
    )

    manager = PanelServerManager(db_path=tmp_path / "snippets.db", port=5090, host="127.0.0.1")

    monkeypatch.setattr(manager_module.requests, "get", lambda *args, **kwargs: SimpleNamespace(status_code=200))
    monkeypatch.setattr(manager, "_find_pid_on_port", lambda: 4321)
    monkeypatch.setattr(manager, "_is_port_in_use", lambda: True)
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    monkeypatch.setattr(manager_module.time, "sleep", lambda _: None)

    killed = {"pid": None, "sig": None}

    def _fake_kill(pid, sig):
        killed["pid"] = pid
        killed["sig"] = sig

    monkeypatch.setattr(os, "kill", _fake_kill)

    assert manager._try_recover_stale_server() is False
    assert killed["pid"] == 4321


def test_try_recover_unhealthy_handles_psutil_access_denied(monkeypatch, tmp_path):
    """AccessDenied during force-kill of stale process should not raise."""

    class FakeNoSuchProcess(Exception):
        pass

    class FakeAccessDenied(Exception):
        pass

    class FakeZombieProcess(Exception):
        pass

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def kill(self):
            raise FakeAccessDenied(self.pid)

    fake_psutil = SimpleNamespace(
        Process=FakeProcess,
        NoSuchProcess=FakeNoSuchProcess,
        AccessDenied=FakeAccessDenied,
        ZombieProcess=FakeZombieProcess,
    )

    manager = PanelServerManager(db_path=tmp_path / "snippets.db", port=5090, host="127.0.0.1")

    def _raise_request_exception(*args, **kwargs):
        raise manager_module.requests.RequestException("unhealthy")

    monkeypatch.setattr(manager_module.requests, "get", _raise_request_exception)
    monkeypatch.setattr(manager, "_find_pid_on_port", lambda: 9876)
    monkeypatch.setattr(manager, "_is_port_in_use", lambda: True)
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    monkeypatch.setattr(manager_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    assert manager._try_recover_stale_server() is False