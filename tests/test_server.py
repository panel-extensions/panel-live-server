"""Tests for the Panel Live Server MCP server."""

import json

import pytest
from fastmcp import Client

from panel_live_server.server import mcp


@pytest.mark.asyncio
async def test_show_pyodide_returns_payload():
    """Test show_pyodide tool returns correct JSON payload."""
    client = Client(mcp)
    async with client:
        result = await client.call_tool("show_pyodide", {"code": "import panel as pn\npn.pane.Markdown('Hello').servable()"})
        assert result is not None
        text = result.content[0].text
        payload = json.loads(text)
        assert payload["tool"] == "show_pyodide"
        assert payload["code"] == "import panel as pn\npn.pane.Markdown('Hello').servable()"
        assert payload["runtime"] == "panel-live-pyodide"


@pytest.mark.asyncio
async def test_show_pyodide_empty_code():
    """Test show_pyodide tool with empty code returns error."""
    client = Client(mcp)
    async with client:
        result = await client.call_tool("show_pyodide", {"code": ""})
        text = result.content[0].text
        payload = json.loads(text)
        assert "error" in payload


@pytest.mark.asyncio
async def test_show_pyodide_with_name_and_description():
    """Test show_pyodide includes name and description in payload."""
    client = Client(mcp)
    async with client:
        result = await client.call_tool("show_pyodide", {
            "code": "print('hello')",
            "name": "Test Viz",
            "description": "A test visualization",
        })
        text = result.content[0].text
        payload = json.loads(text)
        assert payload["name"] == "Test Viz"
        assert payload["description"] == "A test visualization"


@pytest.mark.asyncio
async def test_list_tools():
    """Test that the MCP server exposes expected tools."""
    client = Client(mcp)
    async with client:
        tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        assert "show" in tool_names
        assert "show_pyodide" in tool_names
