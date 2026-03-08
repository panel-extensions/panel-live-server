"""Tests for the Panel Live Server MCP server."""

import json

import pytest
from fastmcp.client import Client
from fastmcp.exceptions import ToolError
from typer.testing import CliRunner

import panel_live_server.server as server_module
from panel_live_server.cli import app
from panel_live_server.server import mcp
from panel_live_server.validation import SecurityError
from panel_live_server.validation import ValidationError


@pytest.mark.asyncio
async def test_list_tools():
    """Test that the MCP server exposes the expected tools."""
    client = Client(mcp)
    async with client:
        tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        assert "show" in tool_names
        assert "validate" in tool_names
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
    """Test show tool returns a JSON payload with expected fields (no prior validate needed)."""
    server_module._validation_cache.clear()
    code = "import panel as pn\npn.pane.Markdown('Hello').servable()"
    client = Client(mcp)
    async with client:
        result = await client.call_tool("show", {"code": code, "method": "panel"})
        text = result.content[0].text
        payload = json.loads(text)
        assert payload["tool"] == "show"
        assert "status" in payload
        assert "url" in payload or "message" in payload


# ---------------------------------------------------------------------------
# validate tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_returns_valid_for_correct_code():
    """validate returns {"valid": true} for clean code."""
    client = Client(mcp)
    async with client:
        result = await client.call_tool("validate", {"code": "x = 1 + 2"})
        data = json.loads(result.content[0].text)
        assert data == {"valid": True}


@pytest.mark.asyncio
async def test_validate_returns_error_for_syntax():
    """validate reports a syntax error."""
    client = Client(mcp)
    async with client:
        result = await client.call_tool("validate", {"code": "def bad syntax"})
        data = json.loads(result.content[0].text)
        assert data["valid"] is False
        assert data["layer"] == "syntax"
        assert "message" in data


@pytest.mark.asyncio
async def test_validate_returns_error_for_security():
    """validate reports a security violation for blocked imports."""
    client = Client(mcp)
    async with client:
        result = await client.call_tool("validate", {"code": "import pickle\npickle.dumps({})"})
        data = json.loads(result.content[0].text)
        assert data["valid"] is False
        assert data["layer"] == "security"


@pytest.mark.asyncio
async def test_validate_returns_error_for_missing_package():
    """validate reports a missing package error."""
    client = Client(mcp)
    async with client:
        result = await client.call_tool("validate", {"code": "import _totally_fake_pkg_xyz_abc"})
        data = json.loads(result.content[0].text)
        assert data["valid"] is False
        assert data["layer"] == "packages"


@pytest.mark.asyncio
async def test_validate_returns_error_for_missing_extension_panel_method():
    """validate reports missing pn.extension() for panel method.

    Uses a comment to trigger find_extensions() substring matching without
    importing a package that may not be installed in the test environment.
    """
    client = Client(mcp)
    async with client:
        result = await client.call_tool(
            "validate",
            {
                "code": "x = 1  # plotly visualization",
                "method": "panel",
            },
        )
        data = json.loads(result.content[0].text)
        assert data["valid"] is False
        assert data["layer"] == "extensions"
        assert "plotly" in data["message"]


@pytest.mark.asyncio
async def test_validate_skips_extension_check_for_jupyter_method():
    """validate does not require pn.extension() for jupyter method (auto-injected at render)."""
    client = Client(mcp)
    async with client:
        result = await client.call_tool(
            "validate",
            {
                "code": "x = 1  # plotly visualization",
                "method": "jupyter",
            },
        )
        data = json.loads(result.content[0].text)
        assert data["valid"] is True


@pytest.mark.asyncio
async def test_validate_result_is_cached():
    """A second validate call with the same code returns the cached result."""
    server_module._validation_cache.clear()
    client = Client(mcp)
    async with client:
        code = "y = 42"
        await client.call_tool("validate", {"code": code})
        assert (code, "jupyter") in server_module._validation_cache
        # Second call hits cache (no exception, same result).
        result = await client.call_tool("validate", {"code": code})
        data = json.loads(result.content[0].text)
        assert data["valid"] is True


# ---------------------------------------------------------------------------
# Typed exception classes (server-side, bypassing MCP transport)
# ---------------------------------------------------------------------------


def test_validation_error_is_tool_error_subclass():
    """ValidationError is a ToolError subclass — FastMCP surfaces it as a tool error."""
    from fastmcp.exceptions import ToolError

    err = ValidationError("[syntax] bad code")
    assert isinstance(err, ToolError)


def test_security_error_is_tool_error_subclass():
    """SecurityError is a ToolError subclass, separate from ValidationError."""
    from fastmcp.exceptions import ToolError

    err = SecurityError("blocked import")
    assert isinstance(err, ToolError)
    assert not isinstance(err, ValidationError)


# ---------------------------------------------------------------------------
# show raises typed errors on validation failures (no App pane opened)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_show_raises_validation_error_on_syntax_error():
    """show raises ValidationError([syntax]) for syntax errors — no App pane opened.

    FastMCP serializes ToolError subclasses to ToolError over the transport;
    the [syntax] prefix in the message is the distinguishing signal.
    """
    server_module._validation_cache.clear()
    client = Client(mcp)
    async with client:
        with pytest.raises(ToolError, match=r"\[syntax\]"):
            await client.call_tool("show", {"code": "def bad syntax"})


@pytest.mark.asyncio
async def test_show_raises_security_error_on_blocked_import():
    """show raises SecurityError for blocked imports — distinct from ValidationError.

    FastMCP serializes to ToolError over the transport; the absence of a [layer]
    prefix (SecurityError has no bracket prefix) marks it as a security violation.
    """
    server_module._validation_cache.clear()
    client = Client(mcp)
    async with client:
        with pytest.raises(ToolError, match="pickle"):
            await client.call_tool("show", {"code": "import pickle\npickle.dumps({})"})


@pytest.mark.asyncio
async def test_show_raises_validation_error_on_missing_package():
    """show raises ValidationError([packages]) for missing packages."""
    server_module._validation_cache.clear()
    client = Client(mcp)
    async with client:
        with pytest.raises(ToolError, match=r"\[packages\]"):
            await client.call_tool("show", {"code": "import _totally_fake_pkg_xyz_abc"})


@pytest.mark.asyncio
async def test_show_raises_tool_error_when_server_not_running():
    """show raises ToolError when the Panel server client is None (valid code)."""
    server_module._validation_cache.clear()
    client = Client(mcp)
    async with client:
        # Override _client after the lifespan has set it, to simulate server absence.
        saved = server_module._client
        server_module._client = None
        try:
            with pytest.raises(ToolError):
                await client.call_tool("show", {"code": "x = 1"})
        finally:
            server_module._client = saved


@pytest.mark.asyncio
async def test_show_caches_validation_and_reuses_on_show():
    """show reuses a cached validate() result — validation functions run only once."""
    from unittest.mock import patch

    server_module._validation_cache.clear()

    call_count = {"n": 0}
    original_ast_check = server_module.ast_check

    def counting_ast_check(code):
        call_count["n"] += 1
        return original_ast_check(code)

    code = "z = 99"
    with patch.object(server_module, "ast_check", side_effect=counting_ast_check):
        client = Client(mcp)
        async with client:
            # First call: validate populates the cache.
            await client.call_tool("validate", {"code": code})
            # Second call: show hits the cache — ast_check not called again.
            # show will then proceed and succeed (Panel server is running via lifespan).
            await client.call_tool("show", {"code": code, "method": "jupyter"})

    assert call_count["n"] == 1, "ast_check should be called exactly once (cached on second call)"


# ---------------------------------------------------------------------------
# show one-shot: works without a prior validate() call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_show_works_without_prior_validate():
    """show succeeds as a one-shot call — no prior validate() required."""
    server_module._validation_cache.clear()
    client = Client(mcp)
    async with client:
        result = await client.call_tool("show", {"code": "x = 1", "method": "jupyter"})
        payload = json.loads(result.content[0].text)
        assert payload["status"] == "success"


@pytest.mark.asyncio
async def test_show_reuses_cache_when_validate_called_first():
    """show reuses the cached validate() result — no double-validation."""
    server_module._validation_cache.clear()
    client = Client(mcp)
    async with client:
        await client.call_tool("validate", {"code": "x = 1", "method": "jupyter"})
        result = await client.call_tool("show", {"code": "x = 1", "method": "jupyter"})
        payload = json.loads(result.content[0].text)
        assert payload["status"] == "success"


@pytest.mark.asyncio
async def test_show_raises_validation_error_for_missing_extension_panel_method():
    """show raises ValidationError([extensions]) for missing pn.extension() (panel method)."""
    server_module._validation_cache.clear()
    client = Client(mcp)
    async with client:
        with pytest.raises(ToolError, match=r"\[extensions\]"):
            await client.call_tool(
                "show",
                {"code": "x = 1  # plotly visualization", "method": "panel"},
            )
