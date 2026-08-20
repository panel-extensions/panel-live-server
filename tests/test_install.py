"""Tests for panel_live_server.install."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from panel_live_server.install import InstallError
from panel_live_server.install import antigravity_config_path
from panel_live_server.install import claude_desktop_config_path
from panel_live_server.install import cline_config_path
from panel_live_server.install import codex_config_path
from panel_live_server.install import copilot_config_path
from panel_live_server.install import cursor_config_path
from panel_live_server.install import gemini_cli_config_path
from panel_live_server.install import jetbrains_config_path
from panel_live_server.install import kilo_code_config_path
from panel_live_server.install import kiro_config_path
from panel_live_server.install import merge_codex_server
from panel_live_server.install import merge_kilo_code_server
from panel_live_server.install import merge_mcp_server
from panel_live_server.install import merge_mistral_vibe_server
from panel_live_server.install import mistral_vibe_config_path
from panel_live_server.install import register_with_claude_code
from panel_live_server.install import resolve_pls_command
from panel_live_server.install import vscode_config_path
from panel_live_server.install import windsurf_config_path


class TestMergeMcpServer:
    """merge_mcp_server() must only ever touch its own key."""

    def test_creates_missing_file_and_parent_dir(self, tmp_path):
        path = tmp_path / "nested" / "claude_desktop_config.json"
        already_installed, _ = merge_mcp_server(path, "/usr/local/bin/pls", ["mcp"])

        assert already_installed is False
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["mcpServers"]["panel-live-server"] == {"command": "/usr/local/bin/pls", "args": ["mcp"]}

    def test_preserves_other_servers(self, tmp_path):
        path = tmp_path / "claude_desktop_config.json"
        path.write_text(json.dumps({"mcpServers": {"other-server": {"command": "foo"}}}), encoding="utf-8")

        merge_mcp_server(path, "/usr/local/bin/pls", ["mcp"])

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["mcpServers"]["other-server"] == {"command": "foo"}
        assert data["mcpServers"]["panel-live-server"] == {"command": "/usr/local/bin/pls", "args": ["mcp"]}

    def test_updates_a_stale_entry(self, tmp_path):
        path = tmp_path / "claude_desktop_config.json"
        path.write_text(json.dumps({"mcpServers": {"panel-live-server": {"command": "/old/pls", "args": ["mcp"]}}}), encoding="utf-8")

        already_installed, _ = merge_mcp_server(path, "/new/pls", ["mcp"])

        assert already_installed is False
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["mcpServers"]["panel-live-server"]["command"] == "/new/pls"

    def test_second_call_with_same_entry_is_a_noop(self, tmp_path):
        path = tmp_path / "claude_desktop_config.json"
        merge_mcp_server(path, "/usr/local/bin/pls", ["mcp"])
        before = path.read_text(encoding="utf-8")

        already_installed, _ = merge_mcp_server(path, "/usr/local/bin/pls", ["mcp"])

        assert already_installed is True
        assert path.read_text(encoding="utf-8") == before

    def test_invalid_json_raises_install_error(self, tmp_path):
        path = tmp_path / "claude_desktop_config.json"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(InstallError):
            merge_mcp_server(path, "/usr/local/bin/pls", ["mcp"])

    def test_preserves_flags_the_existing_entry_passed(self, tmp_path):
        """A --prompts file set up by hand must survive a re-run (found in live testing)."""
        path = tmp_path / "claude_desktop_config.json"
        existing = {"command": "/old/pls", "args": ["mcp", "--prompts", "/home/u/prompts.json"]}
        path.write_text(json.dumps({"mcpServers": {"panel-live-server": existing}}), encoding="utf-8")

        merge_mcp_server(path, "/new/pls", ["mcp"])

        entry = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["panel-live-server"]
        assert entry == {"command": "/new/pls", "args": ["mcp", "--prompts", "/home/u/prompts.json"]}

    def test_explicit_flags_win_over_preserved_ones(self, tmp_path):
        path = tmp_path / "claude_desktop_config.json"
        existing = {"command": "/old/pls", "args": ["mcp", "--prompts", "/old/prompts.json"]}
        path.write_text(json.dumps({"mcpServers": {"panel-live-server": existing}}), encoding="utf-8")

        merge_mcp_server(path, "/new/pls", ["mcp", "--prompts", "/new/prompts.json"])

        entry = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["panel-live-server"]
        assert entry["args"] == ["mcp", "--prompts", "/new/prompts.json"]

    def test_preserving_flags_stays_idempotent(self, tmp_path):
        path = tmp_path / "claude_desktop_config.json"
        existing = {"command": "/usr/local/bin/pls", "args": ["mcp", "--prompts", "/home/u/prompts.json"]}
        path.write_text(json.dumps({"mcpServers": {"panel-live-server": existing}}), encoding="utf-8")

        assert merge_mcp_server(path, "/usr/local/bin/pls", ["mcp"])[0] is True

    def test_preserves_top_level_keys_besides_mcp_servers(self, tmp_path):
        path = tmp_path / "claude_desktop_config.json"
        path.write_text(json.dumps({"mcpServers": {}, "globalShortcut": "Cmd+Shift+Space"}), encoding="utf-8")

        merge_mcp_server(path, "/usr/local/bin/pls", ["mcp"])

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["globalShortcut"] == "Cmd+Shift+Space"


class TestClientConfigShapes:
    """Clients disagree on where the servers live and whether an entry needs a type."""

    def test_vscode_nests_under_servers_with_a_type(self, tmp_path):
        path = tmp_path / "mcp.json"

        merge_mcp_server(path, "/usr/local/bin/pls", ["mcp"], servers_key="servers", entry_type="stdio")

        data = json.loads(path.read_text(encoding="utf-8"))
        assert "mcpServers" not in data
        assert data["servers"]["panel-live-server"] == {
            "type": "stdio",
            "command": "/usr/local/bin/pls",
            "args": ["mcp"],
        }

    def test_vscode_preserves_other_servers_under_its_own_key(self, tmp_path):
        path = tmp_path / "mcp.json"
        path.write_text(json.dumps({"servers": {"other": {"command": "foo"}}}), encoding="utf-8")

        merge_mcp_server(path, "/usr/local/bin/pls", ["mcp"], servers_key="servers", entry_type="stdio")

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["servers"]["other"] == {"command": "foo"}

    def test_typed_entries_stay_idempotent(self, tmp_path):
        path = tmp_path / "mcp.json"
        merge_mcp_server(path, "/usr/local/bin/pls", ["mcp"], servers_key="servers", entry_type="stdio")

        already_installed, _ = merge_mcp_server(path, "/usr/local/bin/pls", ["mcp"], servers_key="servers", entry_type="stdio")

        assert already_installed is True

    def test_cursor_path_is_in_the_home_directory(self):
        assert str(cursor_config_path()).endswith(".cursor/mcp.json")

    def test_vscode_path_is_project_relative(self):
        """VS Code reads MCP config per project, so this must not resolve to $HOME."""
        assert vscode_config_path() == Path(".vscode/mcp.json")
        assert not vscode_config_path().is_absolute()

    def test_extra_fields_are_added_to_the_entry(self, tmp_path):
        """Copilot CLI needs a `tools` list alongside command/args."""
        path = tmp_path / "mcp-config.json"

        merge_mcp_server(path, "/usr/local/bin/pls", ["mcp"], entry_type="local", extra_fields={"tools": ["*"]})

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["mcpServers"]["panel-live-server"] == {
            "type": "local",
            "command": "/usr/local/bin/pls",
            "args": ["mcp"],
            "tools": ["*"],
        }

    def test_extra_fields_stay_idempotent(self, tmp_path):
        path = tmp_path / "mcp-config.json"
        merge_mcp_server(path, "/usr/local/bin/pls", ["mcp"], entry_type="local", extra_fields={"tools": ["*"]})

        already_installed, _ = merge_mcp_server(path, "/usr/local/bin/pls", ["mcp"], entry_type="local", extra_fields={"tools": ["*"]})

        assert already_installed is True


class TestNewClientConfigPaths:
    """Path resolution for the clients added alongside claude/cursor/vscode/claude-code."""

    def test_windsurf(self):
        assert str(windsurf_config_path()).endswith(".codeium/windsurf/mcp_config.json")

    def test_jetbrains(self):
        assert str(jetbrains_config_path()).endswith(".junie/mcp/mcp.json")

    def test_gemini_cli(self):
        assert str(gemini_cli_config_path()).endswith(".gemini/settings.json")

    def test_antigravity(self):
        assert str(antigravity_config_path()).endswith(".gemini/config/mcp_config.json")

    def test_kiro(self):
        assert str(kiro_config_path()).endswith(".kiro/settings/mcp.json")

    def test_kilo_code(self):
        assert str(kilo_code_config_path()).endswith(".config/kilo/kilo.jsonc")

    def test_codex(self):
        assert str(codex_config_path()).endswith(".codex/config.toml")

    def test_mistral_vibe(self):
        assert str(mistral_vibe_config_path()).endswith(".vibe/config.toml")

    def test_copilot_defaults_to_the_dotfile(self, monkeypatch):
        monkeypatch.delenv("COPILOT_HOME", raising=False)
        assert str(copilot_config_path()).endswith(".copilot/mcp-config.json")

    def test_copilot_respects_copilot_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("COPILOT_HOME", str(tmp_path))
        assert copilot_config_path() == tmp_path / "mcp-config.json"

    def test_cline_macos(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        path = cline_config_path()
        assert path.name == "cline_mcp_settings.json"
        assert "saoudrizwan.claude-dev" in path.parts

    def test_cline_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("APPDATA", "C:\\Users\\test\\AppData\\Roaming")
        path = cline_config_path()
        assert path.name == "cline_mcp_settings.json"

    def test_cline_windows_without_appdata_raises(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delenv("APPDATA", raising=False)
        with pytest.raises(InstallError):
            cline_config_path()

    def test_cline_linux(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert str(cline_config_path()).endswith(".config/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json")


class TestMergeKiloCodeServer:
    """Kilo Code packs command+args into one array under a `mcp` key, not `mcpServers`."""

    def test_writes_the_entry(self, tmp_path):
        path = tmp_path / "kilo.jsonc"

        already_installed, entry = merge_kilo_code_server(path, "/usr/local/bin/pls", ["mcp"])

        assert already_installed is False
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["mcp"]["panel-live-server"] == {
            "type": "local",
            "command": ["/usr/local/bin/pls", "mcp"],
            "enabled": True,
        }
        assert entry == data["mcp"]["panel-live-server"]

    def test_second_call_with_same_entry_is_a_noop(self, tmp_path):
        path = tmp_path / "kilo.jsonc"
        merge_kilo_code_server(path, "/usr/local/bin/pls", ["mcp"])

        already_installed, _ = merge_kilo_code_server(path, "/usr/local/bin/pls", ["mcp"])

        assert already_installed is True

    def test_preserves_flags_the_existing_entry_passed(self, tmp_path):
        path = tmp_path / "kilo.jsonc"
        existing = {"type": "local", "command": ["/old/pls", "mcp", "--prompts", "/p.json"], "enabled": True}
        path.write_text(json.dumps({"mcp": {"panel-live-server": existing}}), encoding="utf-8")

        merge_kilo_code_server(path, "/new/pls", ["mcp"])

        entry = json.loads(path.read_text(encoding="utf-8"))["mcp"]["panel-live-server"]
        assert entry["command"] == ["/new/pls", "mcp", "--prompts", "/p.json"]

    def test_invalid_json_raises_install_error(self, tmp_path):
        path = tmp_path / "kilo.jsonc"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(InstallError):
            merge_kilo_code_server(path, "/usr/local/bin/pls", ["mcp"])


class TestMergeCodexServer:
    """Codex nests servers under [mcp_servers.<name>] in a config.toml."""

    def test_writes_the_entry(self, tmp_path):
        path = tmp_path / "config.toml"

        already_installed, entry = merge_codex_server(path, "/usr/local/bin/pls", ["mcp"])

        assert already_installed is False
        assert entry == {"command": "/usr/local/bin/pls", "args": ["mcp"]}
        assert "[mcp_servers.panel-live-server]" in path.read_text(encoding="utf-8")

    def test_preserves_other_toml_content(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text('model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "foo"\n', encoding="utf-8")

        merge_codex_server(path, "/usr/local/bin/pls", ["mcp"])

        text = path.read_text(encoding="utf-8")
        assert 'model = "gpt-5"' in text
        assert "[mcp_servers.other]" in text
        assert "[mcp_servers.panel-live-server]" in text

    def test_second_call_with_same_entry_is_a_noop(self, tmp_path):
        path = tmp_path / "config.toml"
        merge_codex_server(path, "/usr/local/bin/pls", ["mcp"])
        before = path.read_text(encoding="utf-8")

        already_installed, _ = merge_codex_server(path, "/usr/local/bin/pls", ["mcp"])

        assert already_installed is True
        assert path.read_text(encoding="utf-8") == before

    def test_preserves_flags_the_existing_entry_passed(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text('[mcp_servers.panel-live-server]\ncommand = "/old/pls"\nargs = ["mcp", "--prompts", "/p.json"]\n', encoding="utf-8")

        merge_codex_server(path, "/new/pls", ["mcp"])

        assert 'args = ["mcp", "--prompts", "/p.json"]' in path.read_text(encoding="utf-8")

    def test_invalid_toml_raises_install_error(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text("not [ valid toml", encoding="utf-8")

        with pytest.raises(InstallError):
            merge_codex_server(path, "/usr/local/bin/pls", ["mcp"])


class TestMergeMistralVibeServer:
    """Vibe lists servers as [[mcp_servers]] array-of-tables, identified by name."""

    def test_writes_the_entry(self, tmp_path):
        path = tmp_path / "config.toml"

        already_installed, entry = merge_mistral_vibe_server(path, "/usr/local/bin/pls", ["mcp"])

        assert already_installed is False
        assert entry == {
            "name": "panel-live-server",
            "transport": "stdio",
            "command": "/usr/local/bin/pls",
            "args": ["mcp"],
        }
        assert "[[mcp_servers]]" in path.read_text(encoding="utf-8")

    def test_preserves_other_entries_in_the_array(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text('[[mcp_servers]]\nname = "git"\ntransport = "stdio"\ncommand = "uvx"\nargs = []\n', encoding="utf-8")

        merge_mistral_vibe_server(path, "/usr/local/bin/pls", ["mcp"])

        text = path.read_text(encoding="utf-8")
        assert 'name = "git"' in text
        assert 'name = "panel-live-server"' in text

    def test_second_call_with_same_entry_is_a_noop(self, tmp_path):
        path = tmp_path / "config.toml"
        merge_mistral_vibe_server(path, "/usr/local/bin/pls", ["mcp"])

        already_installed, _ = merge_mistral_vibe_server(path, "/usr/local/bin/pls", ["mcp"])

        assert already_installed is True

    def test_updates_a_stale_entry_without_duplicating_it(self, tmp_path):
        path = tmp_path / "config.toml"
        merge_mistral_vibe_server(path, "/old/pls", ["mcp"])

        merge_mistral_vibe_server(path, "/new/pls", ["mcp"])

        text = path.read_text(encoding="utf-8")
        assert text.count("[[mcp_servers]]") == 1
        assert '"/new/pls"' in text

    def test_invalid_toml_raises_install_error(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text("not [ valid toml", encoding="utf-8")

        with pytest.raises(InstallError):
            merge_mistral_vibe_server(path, "/usr/local/bin/pls", ["mcp"])


class TestResolvePlsCommand:
    """The `pls` running the command wins over whatever PATH happens to hold."""

    def test_prefers_the_pls_beside_the_running_interpreter(self, monkeypatch, tmp_path):
        """A dev's editable pls must not lose to a frozen copy shadowing it on PATH."""
        env_bin = tmp_path / "envs" / "default" / "bin"
        env_bin.mkdir(parents=True)
        (env_bin / "pls").touch()
        monkeypatch.setattr(sys, "executable", str(env_bin / "python"))
        monkeypatch.setattr("shutil.which", lambda _: "/somewhere/else/venv/bin/pls")

        assert resolve_pls_command() == str(env_bin / "pls")

    def test_falls_back_to_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "executable", str(tmp_path / "no-pls-here" / "python"))
        monkeypatch.setattr("shutil.which", lambda _: "/usr/local/bin/pls")

        assert resolve_pls_command() == "/usr/local/bin/pls"

    def test_raises_when_nothing_is_found(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "executable", str(tmp_path / "no-pls-here" / "python"))
        monkeypatch.setattr("shutil.which", lambda _: None)

        with pytest.raises(InstallError):
            resolve_pls_command()


class TestRegisterWithClaudeCode:
    """Claude Code owns its own registry, so this shells out rather than editing JSON."""

    def _fake_run(self, returncode=0, stderr="", stdout=""):
        calls = []

        def run(cli_args, **kwargs):
            calls.append(cli_args)
            return subprocess.CompletedProcess(cli_args, returncode, stdout=stdout, stderr=stderr)

        return run, calls

    def test_passes_the_command_after_a_double_dash(self, monkeypatch):
        """Without the `--` separator, pls's own flags would be read as claude's."""
        run, calls = self._fake_run()
        monkeypatch.setattr("shutil.which", lambda _: "/usr/local/bin/claude")
        monkeypatch.setattr(subprocess, "run", run)

        register_with_claude_code("/usr/local/bin/pls", ["mcp", "--prompts", "/p.json"])

        assert calls[0] == [
            "claude",
            "mcp",
            "add",
            "panel-live-server",
            "--",
            "/usr/local/bin/pls",
            "mcp",
            "--prompts",
            "/p.json",
        ]

    def test_missing_claude_cli_raises(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: None)

        with pytest.raises(InstallError, match="claude"):
            register_with_claude_code("/usr/local/bin/pls", ["mcp"])

    def test_surfaces_the_cli_error(self, monkeypatch):
        run, _ = self._fake_run(returncode=1, stderr="already exists")
        monkeypatch.setattr("shutil.which", lambda _: "/usr/local/bin/claude")
        monkeypatch.setattr(subprocess, "run", run)

        with pytest.raises(InstallError, match="already exists"):
            register_with_claude_code("/usr/local/bin/pls", ["mcp"])


class TestClaudeDesktopConfigPath:
    """Path resolution must match the documented per-OS locations."""

    def test_macos(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        assert str(claude_desktop_config_path()).endswith("Library/Application Support/Claude/claude_desktop_config.json")

    def test_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("APPDATA", "C:\\Users\\test\\AppData\\Roaming")
        path = claude_desktop_config_path()
        assert path.name == "claude_desktop_config.json"
        assert "Claude" in path.parts

    def test_windows_without_appdata_raises(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delenv("APPDATA", raising=False)
        with pytest.raises(InstallError):
            claude_desktop_config_path()

    def test_linux(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert str(claude_desktop_config_path()).endswith(".config/Claude/claude_desktop_config.json")
