"""Tests for the Panel Live Server MCP server."""

import json
from unittest.mock import patch

import pytest
from fastmcp.client import Client
from fastmcp.exceptions import ToolError
from typer.testing import CliRunner

import panel_live_server.server as server_module
from panel_live_server.cli import app
from panel_live_server.client import BROWSER_UNAVAILABLE_PREFIX
from panel_live_server.config import get_config
from panel_live_server.config import reset_config
from panel_live_server.screenshot import Capture
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
        assert "screenshot" in tool_names
        # list_packages was removed as an MCP tool (issue #29); the `pls list
        # packages` CLI command remains for humans.
        assert "list_packages" not in tool_names
        assert "validate" not in tool_names
        assert "render" not in tool_names
        assert "show_pyodide" not in tool_names


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
    """show(code, name, method) returns a JSON payload with expected fields."""
    server_module._validation_cache.clear()
    code = "import panel as pn\npn.pane.Markdown('Hello').servable()"
    client = Client(mcp)
    async with client:
        result = await client.call_tool("show", {"code": code, "name": "Test", "method": "server"})
        text = result.content[0].text
        payload = json.loads(text)
        assert payload["tool"] == "show"
        assert "status" in payload
        assert "url" in payload or "message" in payload


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
    err = ValidationError("[syntax] bad code")
    assert isinstance(err, ToolError)


def test_security_error_is_tool_error_subclass():
    """SecurityError is a ToolError subclass, separate from ValidationError."""
    err = SecurityError("blocked import")
    assert isinstance(err, ToolError)
    assert not isinstance(err, ValidationError)


# ---------------------------------------------------------------------------
# show raises typed errors on validation failures (no App pane opened)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code, method, layer, needle",
    [
        ("def bad syntax", "inline", "syntax", None),
        ("import pickle\npickle.dumps({})", "inline", "security", "pickle"),
        ("import _totally_fake_pkg_xyz_abc", "inline", "packages", None),
        ("import panel as pn\npn.extension()\nlayout = pn.Row(pn.pane.Markdown('hi'))\nlayout", "server", "servable", None),
        ("x = 1  # plotly visualization", "server", "extensions", None),
    ],
    ids=["syntax", "blocked-import", "missing-package", "server-no-servable", "missing-extension"],
)
async def test_show_returns_retry_payload(code, method, layer, needle):
    """show() returns a quiet status='retrying' payload (no url) for each static-validation failure."""
    server_module._validation_cache.clear()
    async with Client(mcp) as client:
        result = await client.call_tool("show", {"code": code, "method": method})
        payload = json.loads(result.content[0].text)
        assert payload["status"] == "retrying"
        assert payload["layer"] == layer
        assert "url" not in payload
        if needle:
            assert needle in payload["error_message"]


class TestBriefError:
    """_brief_error() distils the strip text shown next to 'Render failed:' (issue #33)."""

    def test_traceback_uses_exception_line(self):
        tb = "Traceback (most recent call last):\n  File \"<string>\", line 2, in <module>\n    pn.panel(nope)\nNameError: name 'nope' is not defined"
        assert server_module._brief_error(tb) == "NameError: name 'nope' is not defined"

    def test_single_line_message_passes_through(self):
        assert server_module._brief_error("Package 'prophet' is not installed.") == "Package 'prophet' is not installed."

    def test_empty_returns_empty(self):
        assert server_module._brief_error("") == ""
        assert server_module._brief_error("   \n  ") == ""

    def test_long_line_is_truncated(self):
        brief = server_module._brief_error("E: " + "x" * 500)
        assert len(brief) == get_config().brief_error_max_len
        assert brief.endswith("…")

    def test_truncation_length_is_configurable(self, monkeypatch):
        """The cap moved to config.py, so it must actually follow the setting."""
        monkeypatch.setenv("PANEL_LIVE_SERVER_BRIEF_ERROR_MAX_LEN", "40")
        reset_config()
        try:
            assert get_config().brief_error_max_len == 40
            assert len(server_module._brief_error("E: " + "x" * 500)) == 40
        finally:
            reset_config()


class TestTokenCount:
    """_attach_token_count() measures what a tool result costs the model (issue #45)."""

    def test_count_covers_the_payload_as_delivered(self):
        payload = {"tool": "show", "status": "success"}
        server_module._attach_token_count(payload)
        # The count must include its own framing, so the badge never reports
        # less than what the model actually receives.
        assert payload["tokens"] >= server_module._estimate_tokens(len(json.dumps(payload)))

    def test_count_scales_with_payload_size(self):
        small = {"tool": "show", "code": "x = 1"}
        server_module._attach_token_count(small)

        large = {"tool": "show", "code": "x = 1\n" * 25_000}
        server_module._attach_token_count(large)

        assert large["tokens"] > 100 * small["tokens"]
        assert large["tokens"] >= server_module._estimate_tokens(150_000)


@pytest.mark.asyncio
async def test_show_payload_reports_token_cost():
    """Every show() result carries its token cost for the App badge (issue #45)."""
    server_module._validation_cache.clear()
    async with Client(mcp) as client:
        result = await client.call_tool("show", {"code": "x = 1"})
        payload = json.loads(result.content[0].text)
        assert payload["status"] == "success"
        assert isinstance(payload["tokens"], int)
        assert payload["tokens"] > 0


@pytest.mark.asyncio
async def test_show_retry_payload_message_carries_the_error():
    """The retry strip names the failure rather than only saying 'Render failed' (issue #33)."""
    server_module._validation_cache.clear()
    async with Client(mcp) as client:
        result = await client.call_tool("show", {"code": "import _totally_fake_pkg_xyz_abc", "method": "inline"})
        payload = json.loads(result.content[0].text)
        assert payload["message"].startswith("Render failed: ")
        assert "Refining" not in payload["message"]


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


# ---------------------------------------------------------------------------
# Link-only clients — Claude Desktop and Cowork get the "Open in browser" button
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("client_name", "expects_button"),
    [
        ("claude-ai", True),  # Claude Desktop: frame-src CSP blocks the live server
        ("local-agent-mode-desktop", True),  # Cowork: sandboxed iframe blocks the websocket
        ("cursor", False),  # everything else iframes the live URL directly
        ("", False),
    ],
    ids=["claude_desktop", "cowork", "cursor", "unknown"],
)
@pytest.mark.asyncio
async def test_link_only_clients_get_the_open_in_browser_flag(monkeypatch, client_name, expects_button):
    """Clients that cannot iframe localhost are flagged so the App shows the button.

    The inline embed was removed (issue #45): its base64 blob dominated the tool
    result the model had to read, so these clients now always link out instead.
    """
    server_module._validation_cache.clear()
    monkeypatch.setattr(server_module, "_get_mcp_client_name", lambda ctx: client_name)

    async with Client(mcp) as client:
        result = await client.call_tool("show", {"code": "1 + 1"})
        payload = json.loads(result.content[0].text)

    assert payload["status"] == "success"
    assert payload.get("panel_server", False) is expects_button
    assert payload["url"]  # the button and the iframe both need a live URL
    assert "embed_html_gz" not in payload  # no embed is ever sent now


# ---------------------------------------------------------------------------
# screenshot(code=...) — reviewing a draft without showing it (issue #43)
# ---------------------------------------------------------------------------


def _report(result) -> str:
    """Everything the tool said except the standing reminder, which is always last.

    The reminder itself names PAGES and BELOW THE FOLD — it has to, so the model
    knows what those lines mean — so asserting over the whole reply would pass
    whether or not a report was actually emitted.
    """
    texts = [block.text for block in result.content if block.type == "text"]
    return " ".join(texts[:-1])


class TestScreenshotDrafts:
    """The model can look at its own work before the user sees it."""

    @pytest.mark.asyncio
    async def test_screenshot_advertises_the_code_parameter(self):
        """A draft is only reachable if the tool schema actually offers `code`."""
        async with Client(mcp) as client:
            tool = next(t for t in await client.list_tools() if t.name == "screenshot")
            properties = tool.inputSchema["properties"]
            assert "code" in properties
            # snippet_id must have stopped being mandatory, or a draft is unreachable.
            assert "snippet_id" not in tool.inputSchema.get("required", [])

    @pytest.mark.asyncio
    async def test_screenshot_without_code_or_id_is_an_error(self):
        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="code="):
                await client.call_tool("screenshot", {})

    @pytest.mark.asyncio
    async def test_invalid_draft_comes_back_as_a_fixable_message(self):
        """A broken draft returns guidance, not an error box — the user saw nothing."""
        server_module._validation_cache.clear()
        async with Client(mcp) as client:
            result = await client.call_tool("screenshot", {"code": "def bad syntax"})

        assert all(block.type == "text" for block in result.content)
        text = result.content[0].text
        assert "Draft did not render" in text
        assert "screenshot(code=...)" in text

    @pytest.mark.asyncio
    async def test_valid_draft_returns_an_image_and_never_creates_a_snippet(self):
        """The draft path renders via screenshot_code, not via show/create_snippet."""
        server_module._validation_cache.clear()
        async with Client(mcp) as client:
            with (
                patch.object(server_module._client, "screenshot_code", return_value=(Capture(images=[("", b"\x89PNG")]), None, {})) as capture,
                patch.object(server_module._client, "create_snippet") as create,
            ):
                result = await client.call_tool("screenshot", {"code": "1 + 1", "name": "Draft"})

        assert capture.called
        assert not create.called
        assert result.content[0].type == "image"
        assert "REVIEW THIS DRAFT" in result.content[-1].text

    @pytest.mark.asyncio
    async def test_screenshot_advertises_full_page_and_page(self):
        """Neither a tall dashboard nor a tabbed one is reachable unless the schema offers these."""
        async with Client(mcp) as client:
            tool = next(t for t in await client.list_tools() if t.name == "screenshot")
            properties = tool.inputSchema["properties"]
            assert "full_page" in properties
            assert "page" in properties
            # Capturing everything by default floods the caller's context with
            # pages it did not ask about, so more than one screen is opted into.
            assert properties["full_page"].get("default") is False
            assert properties["page"].get("default") == ""

    @pytest.mark.asyncio
    async def test_omitting_them_captures_one_screen_of_the_current_page(self):
        """The defaults must reach the client as (False, ''), not just sit in the schema."""
        server_module._validation_cache.clear()
        capture = Capture(images=[("", b"\x89PNG")])
        async with Client(mcp) as client:
            with patch.object(server_module._client, "screenshot_code", return_value=(capture, None, {})) as shot:
                await client.call_tool("screenshot", {"code": "1 + 1"})

        assert shot.call_args.args[-2:] == (False, "")

    @pytest.mark.asyncio
    async def test_full_page_and_page_reach_the_client(self):
        """A parameter the tool accepts but never forwards is worse than none at all."""
        server_module._validation_cache.clear()
        capture = Capture(images=[("", b"\x89PNG")])
        async with Client(mcp) as client:
            with patch.object(server_module._client, "screenshot_code", return_value=(capture, None, {})) as shot:
                await client.call_tool("screenshot", {"code": "1 + 1", "full_page": True, "page": "Sales"})

        assert shot.call_args.args[-2:] == (True, "Sales")

    @pytest.mark.asyncio
    async def test_several_images_come_back_labelled(self):
        """An unattributed pile of pictures is not an answer about a dashboard."""
        server_module._validation_cache.clear()
        images = [("Sales", b"\x89A"), ("Costs", b"\x89B")]
        capture = Capture(images=images, available_pages=["Sales", "Costs"], captured_pages=["Sales", "Costs"])
        async with Client(mcp) as client:
            with patch.object(server_module._client, "screenshot_code", return_value=(capture, None, {})):
                result = await client.call_tool("screenshot", {"code": "1 + 1", "page": "all"})

        assert [block.type for block in result.content[:4]] == ["text", "image", "text", "image"]
        assert "Sales" in result.content[0].text
        assert "Costs" in result.content[2].text

    @pytest.mark.asyncio
    async def test_pages_not_captured_are_named_beside_the_image(self):
        """A page not captured is absent from the picture exactly as an empty chart is."""
        server_module._validation_cache.clear()
        capture = Capture(images=[("Sales", b"\x89A")], available_pages=["Sales", "Costs", "Forecast"], captured_pages=["Sales"])
        async with Client(mcp) as client:
            with patch.object(server_module._client, "screenshot_code", return_value=(capture, None, {})):
                result = await client.call_tool("screenshot", {"code": "1 + 1"})

        text = _report(result)
        assert "PAGES:" in text
        assert "You have seen: Sales" in text
        assert "Costs" in text and "Forecast" in text

    @pytest.mark.asyncio
    async def test_content_below_the_fold_is_reported_with_a_way_to_get_it(self):
        """One screen of a four-screen dashboard must not read as the whole thing."""
        server_module._validation_cache.clear()
        capture = Capture(images=[("", b"\x89A")], total_tiles=4, captured_tiles=1)
        async with Client(mcp) as client:
            with patch.object(server_module._client, "screenshot_code", return_value=(capture, None, {})):
                result = await client.call_tool("screenshot", {"code": "1 + 1"})

        text = _report(result)
        assert "BELOW THE FOLD: 3 more screens" in text
        assert "full_page=True" in text

    @pytest.mark.asyncio
    async def test_a_truncated_tiled_capture_does_not_suggest_full_page_again(self):
        """full_page was already set; repeating the advice would send the caller in a loop."""
        server_module._validation_cache.clear()
        capture = Capture(images=[("screen 1 of 2", b"\x89A"), ("screen 2 of 2", b"\x89B")], total_tiles=6, captured_tiles=2)
        async with Client(mcp) as client:
            with patch.object(server_module._client, "screenshot_code", return_value=(capture, None, {})):
                result = await client.call_tool("screenshot", {"code": "1 + 1", "full_page": True})

        text = _report(result)
        assert "4 more screens" in text and "tile cap" in text
        assert "full_page=True" not in text

    @pytest.mark.asyncio
    async def test_an_ordinary_chart_gets_no_report_at_all(self):
        """The common case is one small chart that fits — it must cost no extra words."""
        server_module._validation_cache.clear()
        capture = Capture(images=[("", b"\x89A")])
        async with Client(mcp) as client:
            with patch.object(server_module._client, "screenshot_code", return_value=(capture, None, {})):
                result = await client.call_tool("screenshot", {"code": "1 + 1"})

        text = _report(result)
        assert "PAGES:" not in text
        assert "BELOW THE FOLD" not in text

    @pytest.mark.asyncio
    async def test_missing_browser_is_reported_not_retried(self):
        """Rewriting the code cannot install Chromium, so this must not look fixable."""
        server_module._validation_cache.clear()
        failure = (None, f"{BROWSER_UNAVAILABLE_PREFIX}Chromium is not installed. Run: pls install-browser", {})
        async with Client(mcp) as client:
            with patch.object(server_module._client, "screenshot_code", return_value=failure):
                with pytest.raises(ToolError, match="pls install-browser"):
                    await client.call_tool("screenshot", {"code": "1 + 1"})
