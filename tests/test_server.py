"""Tests for the Panel Live Server MCP server."""

import base64
import gzip
import json

import pytest
from fastmcp.client import Client
from fastmcp.exceptions import ToolError
from typer.testing import CliRunner

import panel_live_server.server as server_module
from panel_live_server.cli import app
from panel_live_server.server import _embed_fields
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
        assert "render" in tool_names
        assert "list_packages" in tool_names
        assert "validate" not in tool_names
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
async def test_show_returns_payload_with_code():
    """show(code=...) returns a JSON payload with expected fields (fast single-call mode)."""
    server_module._validation_cache.clear()
    code = "import panel as pn\npn.pane.Markdown('Hello').servable()"
    client = Client(mcp)
    async with client:
        result = await client.call_tool("show", {"code": code, "method": "server"})
        text = result.content[0].text
        payload = json.loads(text)
        assert payload["tool"] == "show"
        assert "status" in payload
        assert "url" in payload or "message" in payload


@pytest.mark.asyncio
async def test_show_streaming_mode_returns_session_id():
    """show() with no code returns a session_id and a /stream URL immediately."""
    client = Client(mcp)
    async with client:
        result = await client.call_tool("show", {"name": "Test viz", "method": "inline"})
        payload = json.loads(result.content[0].text)
        assert payload["tool"] == "show"
        assert payload["status"] == "streaming"
        assert "session_id" in payload
        assert "url" in payload
        assert "/stream" in payload["url"]


# ---------------------------------------------------------------------------
# Validation cache — static checks still run and are cached inside show/render
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_static_validation_result_is_cached():
    """Static validation results are cached by (code, method) across show calls."""
    server_module._validation_cache.clear()
    client = Client(mcp)
    async with client:
        code = "y = 42"
        await client.call_tool("show", {"code": code, "method": "inline"})
        assert (code, "inline") in server_module._validation_cache


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
    """show(code=...) raises ValidationError([syntax]) for syntax errors."""
    server_module._validation_cache.clear()
    client = Client(mcp)
    async with client:
        with pytest.raises(ToolError, match=r"\[syntax\]"):
            await client.call_tool("show", {"code": "def bad syntax"})


@pytest.mark.asyncio
async def test_show_raises_security_error_on_blocked_import():
    """show(code=...) raises SecurityError for blocked imports."""
    server_module._validation_cache.clear()
    client = Client(mcp)
    async with client:
        with pytest.raises(ToolError, match="pickle"):
            await client.call_tool("show", {"code": "import pickle\npickle.dumps({})"})


@pytest.mark.asyncio
async def test_show_raises_validation_error_on_missing_package():
    """show(code=...) raises ValidationError([packages]) for missing packages."""
    server_module._validation_cache.clear()
    client = Client(mcp)
    async with client:
        with pytest.raises(ToolError, match=r"\[packages\]"):
            await client.call_tool("show", {"code": "import _totally_fake_pkg_xyz_abc"})


@pytest.mark.asyncio
async def test_show_raises_validation_error_for_missing_extension_server_method():
    """show(code=..., method='server') raises ValidationError([extensions]) for missing pn.extension()."""
    server_module._validation_cache.clear()
    client = Client(mcp)
    async with client:
        with pytest.raises(ToolError, match=r"\[extensions\]"):
            await client.call_tool(
                "show",
                {"code": "x = 1  # plotly visualization", "method": "server"},
            )


@pytest.mark.asyncio
async def test_show_recovers_when_client_uninitialized():
    """show self-heals when _client is None: lazily (re)starts the server and succeeds."""
    server_module._validation_cache.clear()
    client = Client(mcp)
    async with client:
        saved_client, saved_manager = server_module._client, server_module._manager
        server_module._client = None
        try:
            result = await client.call_tool("show", {"code": "x = 1"})
            payload = json.loads(result.content[0].text)
            assert payload["status"] == "success"
            assert server_module._client is not None  # recovered
        finally:
            server_module._client, server_module._manager = saved_client, saved_manager


@pytest.mark.asyncio
async def test_show_raises_tool_error_when_startup_fails():
    """show raises ToolError when _client is None and lazy startup also fails."""
    from unittest.mock import patch

    server_module._validation_cache.clear()
    client = Client(mcp)
    async with client:
        saved_client, saved_manager = server_module._client, server_module._manager
        server_module._client = None
        try:
            with patch.object(server_module, "_start_panel_server", return_value=(None, None)):
                with pytest.raises(ToolError, match="not running"):
                    await client.call_tool("show", {"code": "x = 1"})
        finally:
            server_module._client, server_module._manager = saved_client, saved_manager


@pytest.mark.asyncio
async def test_show_caches_validation_and_reuses_on_second_call():
    """show reuses a cached static-validation result — ast_check runs only once."""
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
            await client.call_tool("show", {"code": code, "method": "inline"})
            # Second call with same code: hits the cache — ast_check not called again.
            await client.call_tool("show", {"code": code, "method": "inline"})

    assert call_count["n"] == 1, "ast_check should be called exactly once (cached on second call)"


@pytest.mark.asyncio
async def test_show_single_call_succeeds():
    """show(code=...) succeeds as a one-shot call — no separate step required."""
    server_module._validation_cache.clear()
    client = Client(mcp)
    async with client:
        result = await client.call_tool("show", {"code": "x = 1", "method": "inline"})
        payload = json.loads(result.content[0].text)
        assert payload["status"] == "success"


# ---------------------------------------------------------------------------
# render tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_delivers_code_to_session():
    """render(session_id, code) pushes code to a streaming session."""
    from unittest.mock import patch

    client = Client(mcp)
    async with client:
        show_result = await client.call_tool("show", {"name": "Test", "method": "inline"})
        payload = json.loads(show_result.content[0].text)
        session_id = payload["session_id"]

        with patch.object(server_module._client, "render_session", return_value=True) as mock_render:
            result = await client.call_tool("render", {"session_id": session_id, "code": "x = 1"})
            render_payload = json.loads(result.content[0].text)
            assert render_payload["status"] == "success"
            mock_render.assert_called_once_with(session_id, "x = 1")


@pytest.mark.asyncio
async def test_render_returns_pending_on_incomplete_code():
    """render returns status='pending' for incomplete/invalid code so the LLM can keep generating."""
    client = Client(mcp)
    async with client:
        show_result = await client.call_tool("show", {"name": "Test", "method": "inline"})
        session_id = json.loads(show_result.content[0].text)["session_id"]

        result = await client.call_tool("render", {"session_id": session_id, "code": "def bad syntax"})
        payload = json.loads(result.content[0].text)
        assert payload["status"] == "pending"
        assert "session_id" in payload


@pytest.mark.asyncio
async def test_render_raises_tool_error_for_expired_session():
    """render raises ToolError when session_id is not found in the session store."""
    from unittest.mock import patch

    client = Client(mcp)
    async with client:
        with patch.object(server_module._client, "render_session", return_value=False):
            with pytest.raises(ToolError, match="not found or has expired"):
                await client.call_tool("render", {"session_id": "nonexistent-id", "code": "x = 1"})


@pytest.mark.asyncio
async def test_show_streaming_mode_rejects_server_method():
    """show() without code rejects method='server' — streaming only supports inline."""
    client = Client(mcp)
    async with client:
        with pytest.raises(ToolError, match="method='inline'"):
            await client.call_tool("show", {"name": "Test", "method": "server"})


# ---------------------------------------------------------------------------
# server._embed_fields — payload fields controlling how the client renders
# ---------------------------------------------------------------------------


def test_embed_fields_within_cap_round_trips():
    """A small embed is gzip+base64 encoded under 'embed_html_gz' and decodes back."""
    html = "<html><body>hello</body></html>"
    fields = _embed_fields(html, embed_only=True)
    assert set(fields) == {"embed_html_gz"}
    decoded = gzip.decompress(base64.b64decode(fields["embed_html_gz"])).decode("utf-8")
    assert decoded == html


@pytest.mark.parametrize(
    ("embed_html", "embed_only", "oversized", "expected"),
    [
        # Embeddable HTML always wins, regardless of client.
        ("<p>x</p>", True, False, {"embed_html_gz"}),
        ("<p>x</p>", False, False, {"embed_html_gz"}),
        # No embed available (Python-callback app or failed render):
        # embed_only clients get the live-server placeholder...
        ("", True, False, {"panel_server"}),
        (None, True, False, {"panel_server"}),
        # ...while other clients fall back to the live URL already in the payload.
        ("", False, False, set()),
        (None, False, False, set()),
        # Oversized embed is dropped, then falls back the same way.
        ("<p>too big</p>", True, True, {"panel_server"}),
        ("<p>too big</p>", False, True, set()),
    ],
    ids=[
        "embed_claude",
        "embed_other",
        "empty_claude",
        "none_claude",
        "empty_other",
        "none_other",
        "oversized_claude",
        "oversized_other",
    ],
)
def test_embed_fields_branches(monkeypatch, embed_html, embed_only, oversized, expected):
    if oversized:
        monkeypatch.setattr(server_module, "_EMBED_SIZE_CAP", 1)
    assert set(_embed_fields(embed_html, embed_only=embed_only)) == expected


def test_embed_fields_cowork_cap_falls_back_to_link():
    """An embed under the Desktop cap but over Cowork's tight cap falls back gracefully.

    Cowork counts the tool result against its model token budget, so an embed that
    Desktop would render inline must instead drop to the live-server placeholder
    (the "open in browser" link) rather than overflowing Cowork's limit.
    """
    import random

    from panel_live_server.server import _COWORK_EMBED_SIZE_CAP
    from panel_live_server.server import _EMBED_SIZE_CAP

    # High-entropy (incompressible) body so the encoded embed lands between the
    # two caps — repetitive content would gzip away to almost nothing.
    rng = random.Random(0)
    blob = base64.b64encode(bytes(rng.randrange(256) for _ in range(60_000))).decode("ascii")
    big_html = "<html><body>" + blob + "</body></html>"
    encoded_len = len(base64.b64encode(gzip.compress(big_html.encode("utf-8"))).decode("ascii"))
    assert _COWORK_EMBED_SIZE_CAP < encoded_len <= _EMBED_SIZE_CAP

    # Desktop cap: embeds inline. Cowork cap: drops to the placeholder link.
    assert set(_embed_fields(big_html, embed_only=True)) == {"embed_html_gz"}
    assert set(_embed_fields(big_html, embed_only=True, cap=_COWORK_EMBED_SIZE_CAP)) == {"panel_server"}
