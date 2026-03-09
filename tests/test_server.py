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
    """Test packages tool returns a non-empty sorted list of core package names."""
    client = Client(mcp)
    async with client:
        result = await client.call_tool("list_packages", {})
        pkgs = json.loads(result.content[0].text)
        assert isinstance(pkgs, list)
        assert len(pkgs) > 0
        assert all(isinstance(p, str) for p in pkgs)
        # panel must be installed
        names = [p.lower() for p in pkgs]
        assert any("panel" in n for n in names)
        # sorted by name (case-insensitive, hyphens == underscores)
        assert names == sorted(names, key=lambda n: n.replace("-", "_"))
        # default is 'core' — should be a small subset, not all 170+ packages
        assert len(pkgs) < 50


@pytest.mark.asyncio
async def test_packages_tool_category_visualization():
    """Test list_packages with category='visualization' returns only viz packages."""
    client = Client(mcp)
    async with client:
        result = await client.call_tool("list_packages", {"category": "visualization"})
        pkgs = json.loads(result.content[0].text)
        assert isinstance(pkgs, list)
        assert len(pkgs) > 0
        # Should contain well-known viz packages that are installed
        names = {p.lower() for p in pkgs}
        assert "bokeh" in names or "matplotlib" in names or "panel" in names
        # Should NOT contain non-viz packages
        assert len(pkgs) < 50  # much smaller than the full 200+ list


@pytest.mark.asyncio
async def test_packages_tool_category_multiple():
    """Test list_packages with comma-separated categories."""
    client = Client(mcp)
    async with client:
        viz_result = await client.call_tool("list_packages", {"category": "visualization"})
        data_result = await client.call_tool("list_packages", {"category": "data"})
        both_result = await client.call_tool("list_packages", {"category": "visualization,data"})
        viz_pkgs = json.loads(viz_result.content[0].text)
        data_pkgs = json.loads(data_result.content[0].text)
        both_pkgs = json.loads(both_result.content[0].text)
        # Combined should be >= each individual
        assert len(both_pkgs) >= len(viz_pkgs)
        assert len(both_pkgs) >= len(data_pkgs)


@pytest.mark.asyncio
async def test_packages_tool_query_filter():
    """Test list_packages with query narrows results by name substring."""
    client = Client(mcp)
    async with client:
        result = await client.call_tool("list_packages", {"query": "panel"})
        pkgs = json.loads(result.content[0].text)
        assert len(pkgs) > 0
        assert all("panel" in p.lower() for p in pkgs)


@pytest.mark.asyncio
async def test_packages_tool_category_and_query():
    """Test list_packages with both category and query."""
    client = Client(mcp)
    async with client:
        result = await client.call_tool("list_packages", {"category": "panel", "query": "material"})
        pkgs = json.loads(result.content[0].text)
        assert len(pkgs) >= 1
        assert all("material" in p.lower() for p in pkgs)


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
async def test_show_returns_payload_quick():
    """Test show(quick=True) returns a JSON payload with expected fields."""
    server_module._validation_cache.clear()
    server_module._fully_validated.clear()
    code = "import panel as pn\npn.pane.Markdown('Hello').servable()"
    client = Client(mcp)
    async with client:
        result = await client.call_tool("show", {"code": code, "method": "panel", "quick": True})
        text = result.content[0].text
        payload = json.loads(text)
        assert payload["tool"] == "show"
        assert "status" in payload
        assert "url" in payload or "message" in payload


@pytest.mark.asyncio
async def test_show_returns_payload_after_validate():
    """Test show(quick=False) returns a JSON payload after prior validate() call."""
    server_module._validation_cache.clear()
    server_module._fully_validated.clear()
    code = "import panel as pn\npn.pane.Markdown('Hello').servable()"
    client = Client(mcp)
    async with client:
        await client.call_tool("validate", {"code": code, "method": "panel"})
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
    """validate returns {"valid": true} for clean code (including runtime execution)."""
    client = Client(mcp)
    async with client:
        result = await client.call_tool("validate", {"code": "x = 1 + 2"})
        data = json.loads(result.content[0].text)
        assert data == {"valid": True}


@pytest.mark.asyncio
async def test_validate_catches_runtime_error():
    """validate catches runtime errors (e.g. ValueError, TypeError) via code execution."""
    client = Client(mcp)
    async with client:
        # This code is syntactically valid and passes static checks,
        # but raises ValueError at runtime
        result = await client.call_tool("validate", {"code": "int('not_a_number')"})
        data = json.loads(result.content[0].text)
        assert data["valid"] is False
        assert data["layer"] == "runtime"
        assert "ValueError" in data["message"]


@pytest.mark.asyncio
async def test_validate_catches_attribute_error():
    """validate catches AttributeError at runtime."""
    client = Client(mcp)
    async with client:
        result = await client.call_tool("validate", {"code": "x = [1, 2, 3]\nx.nonexistent_method()"})
        data = json.loads(result.content[0].text)
        assert data["valid"] is False
        assert data["layer"] == "runtime"
        assert "AttributeError" in data["message"]


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
async def test_show_quick_raises_validation_error_on_syntax_error():
    """show(quick=True) raises ValidationError([syntax]) for syntax errors."""
    server_module._validation_cache.clear()
    server_module._fully_validated.clear()
    client = Client(mcp)
    async with client:
        with pytest.raises(ToolError, match=r"\[syntax\]"):
            await client.call_tool("show", {"code": "def bad syntax", "quick": True})


@pytest.mark.asyncio
async def test_show_quick_raises_security_error_on_blocked_import():
    """show(quick=True) raises SecurityError for blocked imports."""
    server_module._validation_cache.clear()
    server_module._fully_validated.clear()
    client = Client(mcp)
    async with client:
        with pytest.raises(ToolError, match="pickle"):
            await client.call_tool("show", {"code": "import pickle\npickle.dumps({})", "quick": True})


@pytest.mark.asyncio
async def test_show_quick_raises_validation_error_on_missing_package():
    """show(quick=True) raises ValidationError([packages]) for missing packages."""
    server_module._validation_cache.clear()
    server_module._fully_validated.clear()
    client = Client(mcp)
    async with client:
        with pytest.raises(ToolError, match=r"\[packages\]"):
            await client.call_tool("show", {"code": "import _totally_fake_pkg_xyz_abc", "quick": True})


@pytest.mark.asyncio
async def test_show_raises_tool_error_when_server_not_running():
    """show raises ToolError when the Panel server client is None (valid code)."""
    server_module._validation_cache.clear()
    server_module._fully_validated.clear()
    client = Client(mcp)
    async with client:
        # Override _client after the lifespan has set it, to simulate server absence.
        saved = server_module._client
        server_module._client = None
        try:
            with pytest.raises(ToolError):
                await client.call_tool("show", {"code": "x = 1", "quick": True})
        finally:
            server_module._client = saved


@pytest.mark.asyncio
async def test_show_caches_validation_and_reuses_on_show():
    """show reuses a cached validate() result — validation functions run only once."""
    from unittest.mock import patch

    server_module._validation_cache.clear()
    server_module._fully_validated.clear()

    call_count = {"n": 0}
    original_ast_check = server_module.ast_check

    def counting_ast_check(code):
        call_count["n"] += 1
        return original_ast_check(code)

    code = "z = 99"
    with patch.object(server_module, "ast_check", side_effect=counting_ast_check):
        client = Client(mcp)
        async with client:
            # First call: validate populates the cache + _fully_validated.
            await client.call_tool("validate", {"code": code})
            # Second call: show hits the cache — ast_check not called again.
            await client.call_tool("show", {"code": code, "method": "jupyter"})

    assert call_count["n"] == 1, "ast_check should be called exactly once (cached on second call)"


# ---------------------------------------------------------------------------
# show quick=True: works without a prior validate() call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_show_quick_works_without_prior_validate():
    """show(quick=True) succeeds as a one-shot call — no prior validate() required."""
    server_module._validation_cache.clear()
    server_module._fully_validated.clear()
    client = Client(mcp)
    async with client:
        result = await client.call_tool("show", {"code": "x = 1", "method": "jupyter", "quick": True})
        payload = json.loads(result.content[0].text)
        assert payload["status"] == "success"


@pytest.mark.asyncio
async def test_show_quick_catches_runtime_error():
    """show(quick=True) raises ValidationError([runtime]) for runtime failures."""
    server_module._validation_cache.clear()
    server_module._fully_validated.clear()
    client = Client(mcp)
    async with client:
        with pytest.raises(ToolError, match=r"\[runtime\]"):
            await client.call_tool("show", {"code": "int('not_a_number')", "quick": True})


# ---------------------------------------------------------------------------
# show quick=False (default): requires prior validate() call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_show_default_raises_without_prior_validate():
    """show(quick=False) raises ValidationError if validate() was not called first."""
    server_module._validation_cache.clear()
    server_module._fully_validated.clear()
    client = Client(mcp)
    async with client:
        with pytest.raises(ToolError, match="not been validated"):
            await client.call_tool("show", {"code": "x = 1", "method": "jupyter"})


@pytest.mark.asyncio
async def test_show_default_succeeds_after_validate():
    """show(quick=False) succeeds when validate() was called first."""
    server_module._validation_cache.clear()
    server_module._fully_validated.clear()
    client = Client(mcp)
    async with client:
        await client.call_tool("validate", {"code": "x = 1", "method": "jupyter"})
        result = await client.call_tool("show", {"code": "x = 1", "method": "jupyter"})
        payload = json.loads(result.content[0].text)
        assert payload["status"] == "success"


@pytest.mark.asyncio
async def test_show_quick_raises_validation_error_for_missing_extension_panel_method():
    """show(quick=True) raises ValidationError([extensions]) for missing pn.extension()."""
    server_module._validation_cache.clear()
    server_module._fully_validated.clear()
    client = Client(mcp)
    async with client:
        with pytest.raises(ToolError, match=r"\[extensions\]"):
            await client.call_tool(
                "show",
                {"code": "x = 1  # plotly visualization", "method": "panel", "quick": True},
            )
