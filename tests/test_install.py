"""Tests for panel_live_server.install."""

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from panel_live_server.install import (
    InstallError,
    antigravity_config_path,
    claude_desktop_config_path,
    cline_config_path,
    codex_config_path,
    copilot_config_path,
    cursor_config_path,
    gemini_cli_config_path,
    jetbrains_config_path,
    kilo_code_config_path,
    kiro_config_path,
    merge_codex_server,
    merge_kilo_code_server,
    merge_mcp_server,
    merge_mistral_vibe_server,
    mistral_vibe_config_path,
    register_with_claude_code,
    resolve_pls_command,
    vscode_config_path,
    windsurf_config_path,
)


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

    def test_preserves_fields_the_client_added_itself(self, tmp_path):
        """Kiro writes `disabled`/`autoApprove` next to ours, and a re-run was resetting them."""
        path = tmp_path / "mcp.json"
        existing = {"command": "/old/pls", "args": ["mcp"], "disabled": False, "autoApprove": ["show"]}
        path.write_text(json.dumps({"mcpServers": {"panel-live-server": existing}}), encoding="utf-8")

        merge_mcp_server(path, "/new/pls", ["mcp"])

        entry = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["panel-live-server"]
        assert entry["command"] == "/new/pls"
        assert entry["disabled"] is False
        assert entry["autoApprove"] == ["show"]

    def test_preserving_client_fields_stays_idempotent(self, tmp_path):
        path = tmp_path / "mcp.json"
        existing = {"command": "/usr/local/bin/pls", "args": ["mcp"], "disabled": False, "autoApprove": []}
        path.write_text(json.dumps({"mcpServers": {"panel-live-server": existing}}), encoding="utf-8")

        assert merge_mcp_server(path, "/usr/local/bin/pls", ["mcp"])[0] is True

    def test_does_not_clobber_an_extra_field_the_user_narrowed(self, tmp_path):
        """`tools: ["*"]` is our default, not an override of a hand-picked list."""
        path = tmp_path / "mcp-config.json"
        existing = {"type": "local", "command": "/old/pls", "args": ["mcp"], "tools": ["show"]}
        path.write_text(json.dumps({"mcpServers": {"panel-live-server": existing}}), encoding="utf-8")

        merge_mcp_server(path, "/new/pls", ["mcp"], entry_type="local", extra_fields={"tools": ["*"]})

        entry = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["panel-live-server"]
        assert entry["tools"] == ["show"]

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

    def test_antigravity_defaults_to_the_path_the_shipping_build_reads(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        assert str(antigravity_config_path()).endswith(".gemini/antigravity/mcp_config.json")

    def test_antigravity_falls_back_to_the_documented_path_when_that_is_the_one_present(self, monkeypatch, tmp_path):
        """The docs and the shipped app disagree, so an existing file decides."""
        monkeypatch.setenv("HOME", str(tmp_path))
        documented = tmp_path / ".gemini" / "config" / "mcp_config.json"
        documented.parent.mkdir(parents=True)
        documented.write_text("{}", encoding="utf-8")

        assert antigravity_config_path() == documented

    def test_antigravity_prefers_the_installed_path_when_both_exist(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        for sub in ("config", "antigravity"):
            path = tmp_path / ".gemini" / sub / "mcp_config.json"
            path.parent.mkdir(parents=True)
            path.write_text("{}", encoding="utf-8")

        assert str(antigravity_config_path()).endswith(".gemini/antigravity/mcp_config.json")

    def test_kiro(self):
        assert str(kiro_config_path()).endswith(".kiro/settings/mcp.json")

    def test_kilo_code(self):
        assert str(kilo_code_config_path()).endswith(".config/kilo/kilo.jsonc")

    def test_codex(self, monkeypatch):
        monkeypatch.delenv("CODEX_HOME", raising=False)
        assert str(codex_config_path()).endswith(".codex/config.toml")

    def test_codex_honours_codex_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        assert codex_config_path() == tmp_path / "config.toml"

    def test_mistral_vibe(self, monkeypatch):
        monkeypatch.delenv("VIBE_HOME", raising=False)
        assert str(mistral_vibe_config_path()).endswith(".vibe/config.toml")

    def test_mistral_vibe_honours_vibe_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        assert mistral_vibe_config_path() == tmp_path / "config.toml"

    def test_copilot_defaults_to_the_dotfile(self, monkeypatch):
        monkeypatch.delenv("COPILOT_HOME", raising=False)
        assert str(copilot_config_path()).endswith(".copilot/mcp-config.json")

    def test_copilot_respects_copilot_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("COPILOT_HOME", str(tmp_path))
        assert copilot_config_path() == tmp_path / "mcp-config.json"

    @pytest.fixture(autouse=True)
    def _no_cline_env(self, monkeypatch):
        """Cline reads its location from the environment, so clear it before asserting defaults."""
        for var in ("CLINE_MCP_SETTINGS_PATH", "CLINE_DATA_DIR", "CLINE_DIR"):
            monkeypatch.delenv(var, raising=False)

    def test_cline_defaults_below_the_home_directory(self):
        assert str(cline_config_path()).endswith(".cline/data/settings/cline_mcp_settings.json")

    @pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
    def test_cline_does_not_branch_on_the_platform(self, monkeypatch, platform):
        """Cline 4 shares one file across editors and OSes, unlike Claude Desktop."""
        monkeypatch.setattr(sys, "platform", platform)
        assert str(cline_config_path()).endswith(".cline/data/settings/cline_mcp_settings.json")

    def test_cline_honours_an_explicit_settings_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLINE_MCP_SETTINGS_PATH", str(tmp_path / "custom.json"))
        assert cline_config_path() == tmp_path / "custom.json"

    def test_cline_honours_the_data_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLINE_DATA_DIR", str(tmp_path))
        assert cline_config_path() == tmp_path / "settings" / "cline_mcp_settings.json"

    def test_cline_honours_the_cline_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLINE_DIR", str(tmp_path))
        assert cline_config_path() == tmp_path / "data" / "settings" / "cline_mcp_settings.json"


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

    def test_hyphenated_name_round_trips_through_a_strict_parser(self, tmp_path):
        """`panel-live-server` is a bare key with hyphens, so check a real TOML reader accepts it."""
        path = tmp_path / "config.toml"

        merge_codex_server(path, "/usr/local/bin/pls", ["mcp"])

        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
        assert parsed["mcp_servers"]["panel-live-server"]["command"] == "/usr/local/bin/pls"

    @pytest.mark.parametrize("existing", ["mcp_servers = []\n", "mcp_servers = {}\n", "mcp_servers = 42\n"])
    def test_refuses_a_non_table_mcp_servers_without_touching_the_file(self, tmp_path, existing):
        """Writing a sub-table into an array or inline table emits TOML that no longer parses."""
        path = tmp_path / "config.toml"
        path.write_text(existing, encoding="utf-8")

        with pytest.raises(InstallError, match="not a table"):
            merge_codex_server(path, "/usr/local/bin/pls", ["mcp"])

        assert path.read_text(encoding="utf-8") == existing


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

    def test_converts_the_empty_array_vibe_ships_by_default(self, tmp_path):
        """`mcp_servers = []` is the same key in a different TOML shape, and appending a table to it wrote unparsable TOML."""
        path = tmp_path / "config.toml"
        path.write_text('model = "devstral"\nmcp_servers = []\n', encoding="utf-8")

        merge_mistral_vibe_server(path, "/usr/local/bin/pls", ["mcp"])

        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
        assert parsed["mcp_servers"] == [{"name": "panel-live-server", "transport": "stdio", "command": "/usr/local/bin/pls", "args": ["mcp"]}]
        assert parsed["model"] == "devstral"

    def test_carries_across_servers_written_as_inline_tables(self, tmp_path):
        """Converting the array form must not drop entries the user already had."""
        path = tmp_path / "config.toml"
        path.write_text('mcp_servers = [{name = "git", command = "uvx"}]\n', encoding="utf-8")

        merge_mistral_vibe_server(path, "/usr/local/bin/pls", ["mcp"])

        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
        assert [s["name"] for s in parsed["mcp_servers"]] == ["git", "panel-live-server"]

    @pytest.mark.parametrize("existing", ["mcp_servers = 42\n", "mcp_servers = [1, 2]\n"])
    def test_refuses_a_junk_mcp_servers_without_touching_the_file(self, tmp_path, existing):
        path = tmp_path / "config.toml"
        path.write_text(existing, encoding="utf-8")

        with pytest.raises(InstallError, match="not a list of servers"):
            merge_mistral_vibe_server(path, "/usr/local/bin/pls", ["mcp"])

        assert path.read_text(encoding="utf-8") == existing

    def test_written_file_parses_with_a_strict_parser(self, tmp_path):
        path = tmp_path / "config.toml"

        merge_mistral_vibe_server(path, "/usr/local/bin/pls", ["mcp"])

        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
        assert parsed["mcp_servers"][0]["name"] == "panel-live-server"


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
