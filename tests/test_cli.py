"""Tests for the Panel Live Server CLI."""

import re

from typer.testing import CliRunner

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


class TestPromptsFlag:
    """`--prompts` must reach the prompt layer before server.py renders instructions."""

    def _run_mcp(self, monkeypatch, argv):
        """Invoke `pls mcp` with the transport stubbed, returning the env it set up."""
        import panel_live_server.server as server_module

        captured = {}

        def fake_run(*args, **kwargs):
            # server.py has already been imported (and its instructions rendered)
            # by the time the transport starts, so record the env as it stood then.
            import os

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
        import panel_live_server.server as server_module

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
