"""Tests for the Panel Live Server CLI."""

import json
import os
import re

import pytest
from typer.testing import CliRunner

import panel_live_server.server as server_module
from panel_live_server.cli import app

runner = CliRunner()

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    """Strip ANSI styling from help output.

    Rich styles a flag's first dash separately from the rest, so the raw text
    holds ``\\x1b[1;36m-\\x1b[0m\\x1b[1;36m-prompts`` rather than ``--prompts``.
    Colour is off locally and forced on in CI, so asserting against the raw
    string passes on a laptop and fails on GitHub Actions.
    """
    return _ANSI.sub("", text)


def test_help():
    """Test that --help works."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Panel Live Server" in result.output


def test_serve_help():
    """Test that serve --help works."""
    result = runner.invoke(app, ["serve", "--help"])
    assert result.exit_code == 0
    assert "port" in result.output.lower()


def test_mcp_help():
    """Test that mcp --help works."""
    result = runner.invoke(app, ["mcp", "--help"])
    assert result.exit_code == 0
    assert "transport" in result.output.lower()


def test_mcp_help_documents_the_prompts_flag():
    """`--prompts` is the documented way to point at prompt overrides (issue #50)."""
    result = runner.invoke(app, ["mcp", "--help"])
    assert result.exit_code == 0
    assert "--prompts" in _plain(result.output)


def test_install_claude_help():
    """Test that install claude --help works."""
    result = runner.invoke(app, ["install", "claude", "--help"])
    assert result.exit_code == 0
    assert "claude desktop" in result.output.lower()


class TestInstallClaude:
    """`pls install claude` registers panel-live-server in Claude Desktop's config."""

    def test_writes_the_entry(self, tmp_path):
        config_path = tmp_path / "claude_desktop_config.json"
        result = runner.invoke(
            app,
            ["install", "claude", "--command", "/usr/local/bin/pls", "--config-path", str(config_path)],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["mcpServers"]["panel-live-server"] == {"command": "/usr/local/bin/pls", "args": ["mcp"]}

    def test_second_run_reports_already_registered(self, tmp_path):
        config_path = tmp_path / "claude_desktop_config.json"
        argv = ["install", "claude", "--command", "/usr/local/bin/pls", "--config-path", str(config_path)]
        runner.invoke(app, argv)

        result = runner.invoke(app, argv)

        assert result.exit_code == 0, result.output
        assert "already registered" in result.output.lower()

    def test_prints_the_entry_it_wrote(self, tmp_path):
        """Showing the JSON lets someone set another client up by hand from the same output."""
        config_path = tmp_path / "claude_desktop_config.json"

        result = runner.invoke(
            app,
            ["install", "claude", "--command", "/usr/local/bin/pls", "--config-path", str(config_path)],
        )

        assert result.exit_code == 0, result.output
        assert '"mcpServers"' in result.output
        assert '"panel-live-server"' in result.output
        assert "/usr/local/bin/pls" in result.output

    def test_prompts_flag_is_registered(self, tmp_path):
        config_path = tmp_path / "claude_desktop_config.json"
        prompts_path = tmp_path / "prompts.json"

        result = runner.invoke(
            app,
            [
                "install",
                "claude",
                "--command",
                "/usr/local/bin/pls",
                "--prompts",
                str(prompts_path),
                "--config-path",
                str(config_path),
            ],
        )

        assert result.exit_code == 0, result.output
        entry = json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]["panel-live-server"]
        assert entry["args"] == ["mcp", "--prompts", str(prompts_path)]

    def test_rerun_keeps_a_prompts_file_set_up_by_hand(self, tmp_path):
        """Re-running must not silently drop flags already in the config."""
        config_path = tmp_path / "claude_desktop_config.json"
        existing = {"command": "/old/pls", "args": ["mcp", "--prompts", "/home/u/prompts.json"]}
        config_path.write_text(json.dumps({"mcpServers": {"panel-live-server": existing}}), encoding="utf-8")

        result = runner.invoke(
            app,
            ["install", "claude", "--command", "/new/pls", "--config-path", str(config_path)],
        )

        assert result.exit_code == 0, result.output
        entry = json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]["panel-live-server"]
        assert entry == {"command": "/new/pls", "args": ["mcp", "--prompts", "/home/u/prompts.json"]}

    def test_cursor_writes_mcp_servers(self, tmp_path):
        config_path = tmp_path / "mcp.json"

        result = runner.invoke(
            app,
            ["install", "cursor", "--command", "/usr/local/bin/pls", "--config-path", str(config_path)],
        )

        assert result.exit_code == 0, result.output
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["mcpServers"]["panel-live-server"] == {"command": "/usr/local/bin/pls", "args": ["mcp"]}

    def test_vscode_writes_typed_servers(self, tmp_path):
        """VS Code uses a different key and needs an explicit stdio type."""
        config_path = tmp_path / "mcp.json"

        result = runner.invoke(
            app,
            ["install", "vscode", "--command", "/usr/local/bin/pls", "--config-path", str(config_path)],
        )

        assert result.exit_code == 0, result.output
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["servers"]["panel-live-server"] == {
            "type": "stdio",
            "command": "/usr/local/bin/pls",
            "args": ["mcp"],
        }

    def test_invalid_existing_json_fails_cleanly(self, tmp_path):
        config_path = tmp_path / "claude_desktop_config.json"
        config_path.write_text("{not json", encoding="utf-8")

        result = runner.invoke(
            app,
            ["install", "claude", "--command", "/usr/local/bin/pls", "--config-path", str(config_path)],
        )

        assert result.exit_code == 1
        assert "not valid JSON" in result.output

    @pytest.mark.parametrize("client", ["windsurf", "cline", "jetbrains", "gemini-cli", "antigravity", "kiro"])
    def test_plain_mcp_servers_clients_write_the_entry(self, tmp_path, client):
        """These all share the plain `mcpServers` shape Claude Desktop/Cursor use."""
        config_path = tmp_path / "mcp.json"

        result = runner.invoke(
            app,
            ["install", client, "--command", "/usr/local/bin/pls", "--config-path", str(config_path)],
        )

        assert result.exit_code == 0, result.output
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["mcpServers"]["panel-live-server"] == {"command": "/usr/local/bin/pls", "args": ["mcp"]}

    def test_copilot_writes_a_typed_entry_with_tools(self, tmp_path):
        config_path = tmp_path / "mcp-config.json"

        result = runner.invoke(
            app,
            ["install", "copilot", "--command", "/usr/local/bin/pls", "--config-path", str(config_path)],
        )

        assert result.exit_code == 0, result.output
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["mcpServers"]["panel-live-server"] == {
            "type": "local",
            "command": "/usr/local/bin/pls",
            "args": ["mcp"],
            "tools": ["*"],
        }

    def test_kilo_code_writes_a_combined_command_array(self, tmp_path):
        config_path = tmp_path / "kilo.jsonc"

        result = runner.invoke(
            app,
            ["install", "kilo-code", "--command", "/usr/local/bin/pls", "--config-path", str(config_path)],
        )

        assert result.exit_code == 0, result.output
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["mcp"]["panel-live-server"] == {
            "type": "local",
            "command": ["/usr/local/bin/pls", "mcp"],
            "enabled": True,
        }

    def test_codex_writes_a_toml_table(self, tmp_path):
        config_path = tmp_path / "config.toml"

        result = runner.invoke(
            app,
            ["install", "codex", "--command", "/usr/local/bin/pls", "--config-path", str(config_path)],
        )

        assert result.exit_code == 0, result.output
        text = config_path.read_text(encoding="utf-8")
        assert "[mcp_servers.panel-live-server]" in text
        assert 'command = "/usr/local/bin/pls"' in text

    def test_mistral_vibe_writes_an_array_of_tables_entry(self, tmp_path):
        config_path = tmp_path / "config.toml"

        result = runner.invoke(
            app,
            ["install", "mistral-vibe", "--command", "/usr/local/bin/pls", "--config-path", str(config_path)],
        )

        assert result.exit_code == 0, result.output
        text = config_path.read_text(encoding="utf-8")
        assert "[[mcp_servers]]" in text
        assert 'name = "panel-live-server"' in text

    @pytest.mark.parametrize(
        "client",
        ["windsurf", "cline", "jetbrains", "gemini-cli", "antigravity", "kiro", "copilot", "kilo-code", "codex", "mistral-vibe"],
    )
    def test_help_works(self, client):
        result = runner.invoke(app, ["install", client, "--help"])
        assert result.exit_code == 0, result.output


class TestPromptsFlag:
    """`--prompts` must reach the prompt layer before server.py renders instructions."""

    def _run_mcp(self, monkeypatch, argv):
        """Invoke `pls mcp` with the transport stubbed, returning the env it set up."""
        captured = {}

        def fake_run(*args, **kwargs):
            # server.py has already been imported (and its instructions rendered)
            # by the time the transport starts, so record the env as it stood then.
            captured["PANEL_LIVE_SERVER_PROMPTS_FILE"] = os.environ.get("PANEL_LIVE_SERVER_PROMPTS_FILE")

        monkeypatch.setattr(server_module.mcp, "run", fake_run)
        result = runner.invoke(app, argv)
        assert result.exit_code == 0, result.output
        return captured

    def test_flag_populates_the_prompts_file_env_var(self, monkeypatch, tmp_path):
        path = tmp_path / "prompts.json"
        path.write_text("{}", encoding="utf-8")

        captured = self._run_mcp(monkeypatch, ["mcp", "--prompts", str(path)])
        assert captured["PANEL_LIVE_SERVER_PROMPTS_FILE"] == str(path)

    def test_flag_applies_even_though_server_is_already_imported(self, monkeypatch, tmp_path):
        """server.py renders instructions at import time, and this module imports it.

        Without an explicit re-render in the CLI, --prompts would be silently
        ignored in exactly this situation.
        """
        path = tmp_path / "prompts.json"
        path.write_text('{"library_selection": {"replace": "LIBRARY SELECTION:\\nPlotly only."}}', encoding="utf-8")

        original = server_module.mcp.instructions
        try:
            self._run_mcp(monkeypatch, ["mcp", "--prompts", str(path)])
            assert "Plotly only." in server_module.mcp.instructions
            assert "PRIMARILY write" not in server_module.mcp.instructions
            assert "WORKFLOW:" in server_module.mcp.instructions  # untouched sections survive
        finally:
            server_module.mcp.instructions = original
