"""Tests for the Panel Live Server MCP server."""

import json

import pytest
from fastmcp import Client

from panel_live_server.server import mcp


@pytest.mark.asyncio
async def test_list_tools():
    """Test that the MCP server exposes only the show tool."""
    client = Client(mcp)
    async with client:
        tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        assert "show" in tool_names
        assert "show_pyodide" not in tool_names


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
