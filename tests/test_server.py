"""Tests for the Panel Live Server MCP server."""

import json

import pytest
from fastmcp import Client
from typer.testing import CliRunner

from panel_live_server.cli import app
from panel_live_server.server import mcp


@pytest.mark.asyncio
async def test_list_tools():
    """Test that the MCP server exposes the expected tools."""
    client = Client(mcp)
    async with client:
        tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        assert "show" in tool_names
        assert "list_packages" in tool_names
        assert "show_pyodide" not in tool_names


@pytest.mark.asyncio
async def test_packages_tool_returns_list():
    """Test packages tool returns a non-empty sorted list with name/version dicts."""
    client = Client(mcp)
    async with client:
        result = await client.call_tool("list_packages", {})
        pkgs = json.loads(result.content[0].text)
        assert isinstance(pkgs, list)
        assert len(pkgs) > 0
        assert all("name" in p and "version" in p for p in pkgs)
        # panel must be installed
        names = [p["name"].lower() for p in pkgs]
        assert any("panel" in n for n in names)
        # sorted by name (case-insensitive, hyphens == underscores)
        assert names == sorted(names, key=lambda n: n.replace("-", "_"))


def test_packages_cli_lists_packages():
    """Test pls list packages prints installed packages."""
    runner = CliRunner()
    result = runner.invoke(app, ["list", "packages"])
    assert result.exit_code == 0
    assert "panel" in result.output.lower()


def test_packages_cli_filter():
    """Test pls list packages <filter> narrows results."""
    runner = CliRunner()
    result = runner.invoke(app, ["list", "packages", "panel"])
    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) > 0
    assert all("panel" in line.lower() for line in lines)


@pytest.mark.asyncio
async def test_show_returns_payload():
    """Test show tool returns a JSON payload with expected fields."""
    client = Client(mcp)
    async with client:
        result = await client.call_tool("show", {"code": "import panel as pn\npn.pane.Markdown('Hello').servable()"})
        text = result.content[0].text
        payload = json.loads(text)
        assert payload["tool"] == "show"
        assert "status" in payload
        assert "url" in payload or "message" in payload
