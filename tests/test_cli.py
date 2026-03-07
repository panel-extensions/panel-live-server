"""Tests for the Panel Live Server CLI."""

from typer.testing import CliRunner

from panel_live_server.cli import app

runner = CliRunner()


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
