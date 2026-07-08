"""Panel Live Server - MCP Server.

A standalone MCP server that provides the `show` tool
for executing Python code and rendering visualizations via a Panel web server.
"""

import asyncio
import atexit
import base64
import gzip
import json
import logging
import os
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastmcp import Context
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.apps import AppConfig
from fastmcp.server.apps import ResourceCSP
from fastmcp.tools.tool import ToolResult
from fastmcp.utilities.types import Image
from mcp.types import TextContent

from panel_live_server.client import DisplayClient
from panel_live_server.config import get_config
from panel_live_server.manager import PanelServerManager
from panel_live_server.utils import ExtensionError
from panel_live_server.utils import validate_extension_availability
from panel_live_server.validation import SecurityError
from panel_live_server.validation import ValidationError
from panel_live_server.validation import ast_check
from panel_live_server.validation import check_packages
from panel_live_server.validation import ruff_check

logger = logging.getLogger(__name__)

SHOW_RESOURCE_URI = "ui://panel-live-server/show.html"
SHOW_TEMPLATE_PATH = Path(__file__).parent / "templates" / "show.html"

# Max size of the base64-encoded, gzipped inline embed. Larger payloads are
# dropped in favour of the live-server URL to keep the tool response small.
# Claude Desktop renders the embed in a webview, so it tolerates large payloads.
_EMBED_SIZE_CAP = 150_000

# Cowork counts the embed against its 25,000-token tool-result budget; keep this conservative so oversized embeds fall back to the link instead of erroring.
_COWORK_EMBED_SIZE_CAP = 25_000

# Global instances
_manager: PanelServerManager | None = None
_client: DisplayClient | None = None

# Validation cache: (code, method) → result dict. Session-scoped; reset on restart.
_validation_cache: dict[tuple[str, str], dict] = {}


def _run_validation(code: str, method: str) -> dict:
    """Run static validation layers and cache the result by (code, method).

    Checks (in order):
    1. Syntax — ``ast.parse``
    2. Security — ruff rules + blocked-import list
    3. Package availability — all imports must be installed
    4. Panel extensions — declared via ``pn.extension()`` (``server`` method only)

    Parameters
    ----------
    code : str
        Python code to validate.
    method : str
        Execution method (``"inline"`` or ``"server"``).

    Returns
    -------
    dict
        ``{"valid": True}`` on success, or
        ``{"valid": False, "layer": str, "message": str}`` on the first failure.
    """
    key = (code, method)
    if key in _validation_cache:
        return _validation_cache[key]

    result: dict = {}

    if err := ast_check(code):
        result = {"valid": False, "layer": "syntax", "message": err}
    else:
        try:
            ruff_check(code)
        except SecurityError as e:
            result = {"valid": False, "layer": "security", "message": str(e)}

    if not result:
        if err := check_packages(code):
            result = {"valid": False, "layer": "packages", "message": err}

    if not result and method == "server":
        try:
            validate_extension_availability(code)
        except ExtensionError as e:
            result = {"valid": False, "layer": "extensions", "message": str(e)}

    # method="server" renders only .servable() objects; a bare-expression ending renders empty, so catch it statically and retry rather than show a blank box.
    if not result and method == "server" and ".servable()" not in code:
        result = {
            "valid": False,
            "layer": "servable",
            "message": (
                "method='server' only displays objects marked with .servable(), "
                "but the code calls it nowhere. Either mark the app servable "
                "(e.g. `layout.servable()`), or call show() with method='inline' "
                "and end the code with the object as the last expression."
            ),
        }

    if not result:
        result = {"valid": True}

    _validation_cache[key] = result
    return result


def _retry_payload(
    *,
    name: str,
    description: str,
    method: str,
    zoom: int,
    layer: str,
    error_detail: str,
) -> str:
    """Build a friendly ``status="retrying"`` payload for a failed show() attempt.

    The visualization did not render, but instead of raising a loud ToolError
    (which the MCP App template paints as a big red error box), we return a
    payload with **no url** and ``status="retrying"``. The template renders a
    quiet "Refining visualization…" state — never an error box — while the
    ``error_message``/``recovery`` fields hand the model the detail it needs to
    fix the code and call ``show`` again.
    """
    return json.dumps(
        {
            "tool": "show",
            "name": name,
            "description": description,
            "method": method,
            "zoom": zoom,
            "status": "retrying",
            "layer": layer,
            "message": "Refining visualization…",
            "error_message": f"[{layer}] {error_detail}",
            "recovery": (
                "This code did not render. Do NOT show this error to the user. "
                "Fix the problem described above and call show() again with corrected code. "
                "Only report to the user once show() succeeds."
            ),
        }
    )


def _externalize_url(url: str) -> str:
    """Convert local URLs to externally reachable URLs using config.external_url."""
    if not url:
        return url

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    if host not in {"localhost", "127.0.0.1"}:
        return url

    external_url = get_config().external_url
    if not external_url:
        return url

    path = parsed.path or ""
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{external_url.rstrip('/')}{path}{query}"


def _get_mcp_client_name(ctx: Context | None) -> str:
    """Return the MCP client name from the initialize handshake (lowercased).

    Claude Desktop sends ``'claude-ai'``. Returns empty string if unavailable.
    """
    try:
        return ctx.request_context.session.client_params.clientInfo.name.lower()  # type: ignore[union-attr]
    except Exception:
        return ""


def _embed_fields(embed_html: str | None, embed_only: bool, cap: int | None = None) -> dict[str, str | bool]:
    """Build the payload fields that tell the client how to render the result.

    ``embed_html`` is self-contained HTML, or ``""``/``None`` when embedding is
    not possible (e.g. apps with Python callbacks, or a failed render). When the
    compressed embed fits within ``cap`` it is returned for inline ``srcdoc``
    rendering. Otherwise ``embed_only`` clients (whose iframes can't reach the
    live server) get a placeholder signal, while other clients fall back to the
    live URL already present in the payload.

    ``cap`` is the max length of the encoded embed. It varies by client:
    Desktop tolerates large embeds (``_EMBED_SIZE_CAP``); Cowork has a tight
    token budget (``_COWORK_EMBED_SIZE_CAP``), so oversized embeds gracefully
    fall back to the link rather than overflowing its limit.
    """
    if cap is None:
        cap = _EMBED_SIZE_CAP
    if embed_html:
        encoded = base64.b64encode(gzip.compress(embed_html.encode("utf-8"))).decode("ascii")
        if len(encoded) <= cap:
            return {"embed_html_gz": encoded}
    if embed_only:
        return {"panel_server": True}
    return {}


def _start_panel_server() -> tuple[PanelServerManager | None, DisplayClient | None]:
    """Start the Panel server subprocess and create a client."""
    config = get_config()

    manager = PanelServerManager(
        db_path=config.db_path,
        port=config.port,
        host=config.host,
        max_restarts=config.max_restarts,
    )

    if not manager.start():
        logger.error("Failed to start Panel server")
        return None, None

    client = DisplayClient(base_url=manager.get_base_url())
    return manager, client


_cleaned_up = False


def _cleanup():
    """Stop the Panel server and close the client. Idempotent — safe to call multiple times."""
    global _manager, _client, _cleaned_up
    if _cleaned_up:
        return
    _cleaned_up = True
    if _client:
        logger.info("Cleaning up Panel Live Server client")
        _client.close()
        _client = None
    if _manager:
        logger.info("Stopping Panel Live Server")
        _manager.stop()
        _manager = None


def _sigterm_handler(signum, frame):
    """Handle SIGTERM by running cleanup before exit.

    Python does not call atexit handlers on SIGTERM by default. Installing this
    handler ensures the Panel server subprocess is stopped whenever the MCP
    server is killed (e.g. by Claude restarting it), preventing orphan processes
    that would serve stale code on the next startup.
    """
    _cleanup()
    # Restore the default handler and re-raise so the process exits with SIGTERM.
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    os.kill(os.getpid(), signal.SIGTERM)


signal.signal(signal.SIGTERM, _sigterm_handler)


@asynccontextmanager
async def app_lifespan(app):
    """MCP server lifespan - eagerly start the Panel server."""
    global _manager, _client

    logger.info("Starting Panel Live Server...")
    _manager, _client = _start_panel_server()

    if _manager:
        atexit.register(_cleanup)
        feed_url = _externalize_url(f"http://{_manager.host}:{_manager.port}/feed")
        # Print to stderr so it's visible even in stdio MCP mode
        print(f"\n  Panel Live Server is running.\n  Feed: {feed_url}\n", file=sys.stderr, flush=True)  # noqa: T201
        logger.info(f"Panel Live Server started — feed: {feed_url}")
    else:
        logger.warning("Panel Live Server failed to start - show tool will not work")

    # Warn early if the screenshot browser is missing, so users find out now
    # rather than mid-screenshot. The check uses Playwright's sync API, so run
    # it in a worker thread to stay off the event loop.
    try:
        from panel_live_server.screenshot import is_browser_installed

        if not await asyncio.to_thread(is_browser_installed):
            msg = "The `screenshot` tool needs Chromium, which is not installed. Run `pls install-browser` to enable it."
            print(f"\n  {msg}\n", file=sys.stderr, flush=True)  # noqa: T201
            logger.warning(msg)
    except Exception:
        logger.debug("Could not check screenshot browser availability", exc_info=True)

    try:
        yield
    finally:
        _cleanup()


_CLAUDE_DESKTOP_INSTRUCTIONS = (
    "IF YOU ARE CLAUDE DESKTOP\n"
    "Visualizations are embedded inline in the chat via srcdoc — no live server connection. "
    "Follow these rules strictly:\n"
    "- For ANY interactive visualization (sliders, dropdowns, buttons): use Bokeh widgets "
    "  (Slider, Select, CheckboxGroup) with jslink or CustomJS. "
    "  Do NOT use pn.bind, @pn.depends, param.watch, or .servable() — "
    "  these require a live Python server and will NOT work inline.\n"
    "- Use method='inline' for all plots, static or interactive.\n"
    "- Use method='server' ONLY for complex Panel dashboards that truly need a running Python server "
    "  (e.g. FastListTemplate, real-time streaming, database queries). "
    "  The user will need to open these via the 'Show Visualization' link.\n\n"
    "IF YOU ARE ANY OTHER CLIENT (Cursor, VS Code, etc.)\n"
    "Visualizations load via iframe.src — the live server is always reachable. "
    "Use Panel reactive patterns (pn.bind, @pn.depends) freely. "
    "Use method='server' for any interactive Panel app.\n\n"
)

mcp = FastMCP(
    "Panel Live Server",
    instructions=(
        "Panel Live Server executes Python code snippets and renders the resulting "
        "visualizations as live, interactive web pages.\n\n"
        "WORKFLOW:\n"
        "1. DISCOVER (once per session): Call `list_packages()` to see what Python\n"
        "   packages are installed. The environment is fixed and cannot be modified.\n"
        "2. SHOW: Call `show(code, name, method)` to render a visualization.\n"
        "   Static validation (syntax, security, packages) runs in ~50 ms.\n"
        "   The iframe loads immediately via Panel's WebSocket — no prior validate() needed.\n\n"
        "DO NOT write the visualization code to a file, script, notebook, or `examples/`\n"
        "directory, and do not run it in a separate shell/REPL. Pass the code string\n"
        "straight to `show(code=...)` — it executes the code for you. Creating files\n"
        "clutters the user's repository and is not wanted.\n\n"
        "LIBRARY SELECTION (prefer in this order when suitable):\n"
        "- hvPlot: quick interactive plots from DataFrames (.plot API)\n"
        "- HoloViews: advanced composable, interactive visualizations\n"
        "- Panel: dashboards, data apps, complex layouts (use method='server')\n"
        "- Matplotlib: publication-quality static plots\n"
        "- Plotly: interactive charts with 3D, hover\n"
        "- ECharts (pn.pane.ECharts): modern business-quality charts with data transitions and animations\n"
        "- Bokeh: low-level interactive web plots\n"
        "- deck.gl (pn.pane.DeckGL): large-scale geospatial and 3D data visualization\n"
        "Always verify the library is installed via `list_packages` first.\n\n" + _CLAUDE_DESKTOP_INSTRUCTIONS + "OUTPUT\n"
        "After calling `show`, ALWAYS present the returned URL to the user as a "
        "clickable Markdown link: [Show Visualization](url)\n\n"
        "ERRORS\n"
        "`show` raises `SecurityError` for blocked imports or dangerous patterns — "
        "these require a substantive code rewrite, not a retry. "
        "`show` raises `ValidationError` for syntax errors, missing packages, or "
        "missing Panel extension declarations — fix the reported issue and try again."
    ),
    lifespan=app_lifespan,
)


# --- Resources ---


def _build_frame_domains() -> list[str]:
    """Build the CSP frame-src list, adding the Panel server port and external origin."""
    config = get_config()
    port = config.port
    domains = [
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        f"https://127.0.0.1:{port}",
        f"https://localhost:{port}",
    ]
    external_url = config.external_url
    if external_url:
        parsed = urlparse(external_url)
        if parsed.hostname:
            origin = f"{parsed.scheme}://{parsed.hostname}"
            if parsed.port:
                origin += f":{parsed.port}"
            if origin not in domains:
                domains.append(origin)
    return domains


@mcp.resource(
    SHOW_RESOURCE_URI,
    app=AppConfig(
        csp=ResourceCSP(
            resource_domains=[
                "'unsafe-inline'",
                "https://unpkg.com",
                "https://cdn.bokeh.org",
                "https://cdn.holoviz.org",
                "https://cdn.jsdelivr.net",
                "https://cdn.plot.ly",
            ],
            frame_domains=_build_frame_domains(),
        )
    ),
)
def show_view() -> str:
    """Return the HTML resource used by the show MCP App."""
    return SHOW_TEMPLATE_PATH.read_text(encoding="utf-8")


# --- Package categories for list_packages filtering ---

_PACKAGE_CATEGORIES: dict[str, set[str]] = {
    "visualization": {
        "altair",
        "bokeh",
        "colorcet",
        "datashader",
        "geoviews",
        "great-tables",
        "holoviews",
        "hvplot",
        "matplotlib",
        "panel",
        "panel-graphic-walker",
        "panel-material-ui",
        "plotly",
        "plotnine",
        "pydeck",
        "seaborn",
        "vega-datasets",
    },
    "data": {
        "duckdb",
        "numpy",
        "pandas",
        "polars",
        "pyarrow",
        "scipy",
        "scikit-learn",
        "scikit-image",
        "xarray",
        "shapely",
        "yfinance",
        "hvsampledata",
        "pooch",
    },
    "panel": {
        "panel",
        "panel-material-ui",
        "panel-graphic-walker",
        "panel-full-calendar",
        "panel-neuroglancer",
        "panel-precision-slider",
        "panel-splitjs",
        "panel-web-llm",
        "param",
        "pyviz-comms",
    },
}

# "core" is the union of all named categories — used as the default.
_PACKAGE_CATEGORIES["core"] = _PACKAGE_CATEGORIES["visualization"] | _PACKAGE_CATEGORIES["data"] | _PACKAGE_CATEGORIES["panel"]


# --- Tools ---


async def _ensure_client_ready(ctx: Context | None) -> None:
    """Lazily start the Panel server if not running, then verify health. Raises ToolError on failure."""
    global _manager, _client

    if not _client:
        logger.warning("Panel Live Server client is not initialized — attempting lazy startup")
        _manager, _client = _start_panel_server()
        if _manager:
            atexit.register(_cleanup)

    if not _client:
        config = get_config()
        raise ToolError(
            f"Panel Live Server is not running. Restart the MCP server. "
            f"Ensure port {config.port} is not already in use."
        )

    if not _client.is_healthy():
        if ctx:
            await ctx.info("Panel Live Server is not healthy, attempting restart...")
        if _manager and _manager.restart():
            _client.close()
            _client = DisplayClient(base_url=_manager.get_base_url())
        else:
            config = get_config()
            raise ToolError(
                f"Panel Live Server is not healthy and failed to restart. "
                f"Kill any process on port {config.port} and restart the MCP server."
            )


@mcp.tool(name="list_packages")
async def list_packages(
    category: str = "core",
    query: str = "",
    include_versions: bool = False,
    ctx: Context | None = None,
) -> list[str] | list[dict[str, str]]:
    """List Python packages installed in the server environment.

    IMPORTANT — call this tool ONCE at the start of every session before writing
    any visualization code, so you know exactly what libraries are available.
    The environment is fixed — packages cannot be installed or changed.

    Parameters
    ----------
    category : str, optional
        Comma-separated list of categories to filter by.
        Valid categories: ``"visualization"``, ``"data"``, ``"panel"``, ``"core"``.
        Default ``"core"`` returns the union of visualization + data + panel
        (~30 packages). Use ``""`` (empty string) to return all installed packages.
    query : str, optional
        Case-insensitive substring filter on package name.
        Example: ``"panel"`` returns only packages with "panel" in their name.
    include_versions : bool, optional
        If ``True``, return ``{"name": ..., "version": ...}`` dicts.
        Default ``False`` returns a flat list of package name strings to
        minimize context window usage.

    Returns
    -------
    list[str] | list[dict[str, str]]
        Sorted list of package names (default) or ``{"name": ..., "version": ...}``
        dicts when ``include_versions=True``.

    Examples
    --------
    >>> list_packages()
    ["bokeh", "hvplot", "numpy", "panel", ...]
    >>> list_packages(category="visualization")
    ["bokeh", "matplotlib", ...]
    >>> list_packages(category="")  # all installed packages
    ["aiofile", "anyio", ...]
    >>> list_packages(query="panel", include_versions=True)
    [{"name": "panel", "version": "1.6.0"}, ...]
    """
    from importlib.metadata import distributions

    pkgs = sorted(
        ({"name": dist.metadata["Name"], "version": dist.metadata["Version"]} for dist in distributions()),
        key=lambda d: d["name"].lower().replace("-", "_"),
    )

    # Filter by category
    if category:
        requested = {c.strip().lower() for c in category.split(",") if c.strip()}
        allowed_names: set[str] = set()
        for cat in requested:
            if cat in _PACKAGE_CATEGORIES:
                allowed_names |= _PACKAGE_CATEGORIES[cat]
        if allowed_names:
            pkgs = [p for p in pkgs if p["name"].lower().replace("_", "-") in allowed_names]

    # Filter by name substring
    if query:
        query_lower = query.lower()
        pkgs = [p for p in pkgs if query_lower in p["name"].lower()]

    if include_versions:
        return pkgs
    return [p["name"] for p in pkgs]


@mcp.tool(name="show", app=AppConfig(resource_uri=SHOW_RESOURCE_URI))
async def show(
    code: str,
    name: str = "",
    description: str = "",
    method: Literal["inline", "server"] = "inline",
    zoom: int = 75,
    ctx: Context | None = None,
) -> str:
    """Display Python code as a live, interactive visualization.

    Runs static validation (syntax, security, packages, extensions) in ~50 ms,
    stores the snippet, and returns the visualization URL. The iframe loads
    immediately via Panel's WebSocket — the user sees a loading indicator then
    the rendered visualization, with no prior ``validate()`` call needed.

    Always call this tool when the user asks to show, display, plot, or
    visualize anything.

    IMPORTANT — pass the Python code DIRECTLY as the ``code`` argument. Do NOT
    write it to a file in the user's project first, do NOT create scripts,
    notebooks, or ``examples/`` files, and do NOT run it in a separate shell.
    This tool executes the code itself; creating files is unwanted side-effect
    clutter in the user's repository.

    IMPORTANT — always provide a short ``name`` (e.g. "Temperature chart") so
    the visualization is easy to find in the feed.

    IMPORTANT — after calling this tool, always present the returned ``url`` to
    the user as a clickable Markdown link: ``[Show Visualization](url)``

    Parameters
    ----------
    code : str
        Python code to execute.
        For ``"inline"`` method: the last expression is displayed. It must be
        fully dedented (no leading whitespace on top-level statements).
        For ``"server"`` method: call ``.servable()`` on objects to display.
    name : str, optional
        Short display name shown in the feed (e.g. "Sales chart 2024").
        Always provide this — unnamed visualizations are hard to track.
    description : str, optional
        One-sentence description of what the visualization shows.
    method : {"inline", "server"}, default "inline"
        Execution mode:

        - ``"inline"``: displays the last expression's result. Use for standard
          plots, DataFrames, and objects that do NOT import Panel directly.
        - ``"server"``: displays objects marked ``.servable()``. Use when the
          code imports and uses Panel to build dashboards or complex layouts.
    zoom : {100, 75, 50, 25}, default 75
        Initial zoom level for the preview pane. 75 fits most charts and
        dashboards. Use 50 for full-page templates, 25 for very wide apps.

    Returns
    -------
    str
        JSON payload for MCP App rendering, including the visualization URL.
    """
    global _manager, _client

    client_name = _get_mcp_client_name(ctx)
    is_cowork = client_name.startswith("local-agent-mode-")
    embed_only = client_name == "claude-ai" or is_cowork

    _valid_zooms = [25, 50, 75, 100]
    zoom = min(_valid_zooms, key=lambda z: abs(z - zoom))

    await _ensure_client_ready(ctx)

    # Static validation failure: return a quiet retry payload (not a loud error) so the App pane shows a friendly "Refining…" state while the model fixes the code.
    validation = _run_validation(code, method)
    if not validation["valid"]:
        return _retry_payload(
            name=name,
            description=description,
            method=method,
            zoom=zoom,
            layer=validation.get("layer", "validation"),
            error_detail=validation.get("message", "Validation failed."),
        )

    try:
        response = _client.create_snippet(
            code=code,
            name=name,
            description=description,
            method=method,
            validated=False,
        )
        url = _externalize_url(response.get("url", ""))

        # Runtime failure: the code ran and raised — same quiet retry treatment, hand the traceback to the model, show no error box.
        if error_message := response.get("error_message", None):
            return _retry_payload(
                name=name,
                description=description,
                method=method,
                zoom=zoom,
                layer="runtime",
                error_detail=error_message,
            )

        payload = {
            "tool": "show",
            "name": name,
            "description": description,
            "method": method,
            "zoom": zoom,
            "url": url,
            "code": code,
        }

        snippet_id = response.get("id", "")

        if embed_only and snippet_id:
            embed_html = await asyncio.to_thread(_client.get_embed_html, snippet_id)
            cap = _COWORK_EMBED_SIZE_CAP if is_cowork else _EMBED_SIZE_CAP
            payload.update(_embed_fields(embed_html, embed_only, cap))

        payload["status"] = "success"
        payload["message"] = "Visualization created successfully."
        payload["hint"] = (
            "Follow-up handling for this visualization:\n"
            f"- If the user only ASKS for information about it (where something peaks, "
            "which element is largest, colors, positions, etc.) without wanting any "
            f'change, call `screenshot(snippet_id="{snippet_id}")` to SEE the rendered '
            "image and answer from it. Do NOT recompute from the code or re-run the data "
            "— the rendered plot can differ from the raw data (row order, axis inversion, "
            "sorting, binning).\n"
            "- If the user wants to MODIFY the visualization (change colors, add a "
            "series, adjust layout, etc.), write the updated code and call `show` again."
        )
        return json.dumps(payload)

    except SecurityError:
        raise
    except ValidationError:
        raise
    except (SyntaxError, ExtensionError) as e:
        raise ValidationError(str(e)) from e
    except ValueError as e:
        raise ValidationError(
            f"[packages] {e}\nDo NOT install packages or change the environment. "
            "Call list_packages to see what is available, then rewrite using an installed library."
        ) from e
    except Exception as e:
        logger.exception("Error creating visualization: %s", e)
        if ctx:
            await ctx.error(f"Failed to create visualization: {e}")
        raise ToolError(f"Failed to create visualization: {e!s}. Check that the Panel server is running and the code is valid Python.") from e


@mcp.tool(name="screenshot")
async def screenshot(
    snippet_id: str,
    width: int = 1200,
    height: int = 800,
    full_page: bool = False,
    ctx: Context | None = None,
) -> ToolResult:
    """See an EXISTING visualization — returns a PNG image of it to you (the LLM).

    Pass the `snippet_id` that `show` returned; this tool screenshots that
    already-rendered `/view` page and hands you the picture so you can answer a
    follow-up question about how it LOOKS. It does NOT create or modify anything
    and is NOT a substitute for `show` — the user already has the interactive
    visualization in their browser.

    ════════════════════════════════════════════════════════════════════════
    CRITICAL RULE — answering questions ABOUT a visualization's appearance:
    ════════════════════════════════════════════════════════════════════════
    When the user asks where something is, which element is biggest/smallest,
    what color/position/shape something has, or anything about how the chart
    LOOKS, you MUST call this tool and answer from the returned image.

    You MUST NOT answer such questions by reading the code, recomputing from the
    raw data, or re-running the snippet in a Python tool. THAT IS CHEATING AND IS
    USUALLY WRONG, because the rendered plot is NOT the same as the raw data:
      - heatmaps flip/reverse the row order (Row 0 often renders at the BOTTOM)
      - axes get inverted, categories get sorted, histograms bin/group values
      - color mapping, stacking, and layout change what is visually "highest"
    The raw-data answer and the on-screen answer frequently DISAGREE. The image
    is the only ground truth for a question about appearance — so look at it.

    Do not add `np.random.seed(...)` or otherwise make data deterministic just so
    you can recompute it; read the answer off the actual picture.

    ════════════════════════════════════════════════════════════════════════
    IMAGE QUALITY — when the picture is not enough:
    ════════════════════════════════════════════════════════════════════════
    After receiving the screenshot, check whether it is clear enough to answer:
      - Is the chart blurry or pixelated?
      - Is the relevant detail (a label, a tick value, a legend entry) too small
        to read confidently?
      - Is the area of interest clipped or off-screen?

    If YES — the image is not reliable enough — do NOT guess from it.
    Instead, answer the question directly from the code and data (compute
    the value, read the label, inspect the structure). A code-derived answer
    is better than a wrong guess from a bad image.

    If the image is fine, always prefer it over recomputing (see CRITICAL RULE
    above — rendered output and raw data frequently disagree).

    WHEN TO USE — a follow-up question about an already-shown visualization that
    can only be answered by seeing it (random/dynamic data, or visual position):
        · wave/line chart  → "where does it peak?", "where is the lowest dip?"
        · bar chart        → "which bar is the tallest?", "which category leads?"
        · scatter plot     → "where are the outliers?", "how spread out are the points?"
        · heatmap          → "which cell has the highest value?"
        · pie/donut chart  → "which slice is the largest?"
        · histogram        → "where is the distribution centered?"
        · any chart        → "what color is X?", "what does the legend say?"

    Typical loop: `validate` → `show` (returns id) → `screenshot(snippet_id=id)`
    when the user asks something visual you can't read off the code.

    Parameters
    ----------
    snippet_id : str
        Id of the visualization to screenshot, as returned by `show`.
    width : int, default 1200
        Browser viewport width in pixels.
    height : int, default 800
        Browser viewport height in pixels.
    full_page : bool, default False
        If ``True``, capture the full scrollable page rather than just the
        viewport. Useful for tall dashboards.

    Returns
    -------
    Image
        PNG screenshot of the rendered visualization.
    """
    if not snippet_id:
        raise ToolError("Provide the snippet_id returned by show() to screenshot its rendered page.")

    # Capture the existing snippet's rendered /view page as a PNG.
    # The endpoint 404s if the id is unknown.
    png, error = await asyncio.to_thread(_client.get_screenshot, snippet_id, width, height, full_page)
    if error:
        raise ToolError(f"Screenshot failed: {error}")
    if not png:
        raise ToolError("Screenshot capture returned no image data.")

    reminder = (
        "IMAGE QUALITY CHECK — before answering:\n"
        "· Blurry, pixelated, or clipped? → answer from the code/data instead.\n"
        "· Text/labels too small to read confidently? → answer from the code/data instead.\n"
        "· Image is clear and complete? → answer from THIS image only. "
        "Do NOT recompute from raw data — rendered output and raw data frequently disagree "
        "(row order, axis inversion, sorting, binning)."
    )
    return ToolResult(
        content=[
            Image(data=png, format="png").to_image_content(),
            TextContent(type="text", text=reminder),
        ]
    )
