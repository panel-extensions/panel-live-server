"""Panel Live Server - MCP Server.

A standalone MCP server that provides the `show` tool
for executing Python code and rendering visualizations via a Panel web server.
"""

import atexit
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
    4. Panel extensions — declared via ``pn.extension()`` (``panel`` method only)

    Parameters
    ----------
    code : str
        Python code to validate.
    method : str
        Execution method (``"jupyter"`` or ``"panel"``).

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

    if not result and method == "panel":
        try:
            validate_extension_availability(code)
        except ExtensionError as e:
            result = {"valid": False, "layer": "extensions", "message": str(e)}

    if not result:
        result = {"valid": True}

    _validation_cache[key] = result
    return result


def _externalize_url(url: str) -> str:
    """Convert local URLs to externally reachable proxy/Codespaces URLs."""
    if not url:
        return url

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    if host not in {"localhost", "127.0.0.1"}:
        return url

    port = parsed.port
    if not port:
        return url

    config = get_config()

    proxy_base = os.getenv("JUPYTER_SERVER_PROXY_URL") or config.jupyter_server_proxy_url
    if proxy_base:
        return f"{proxy_base.rstrip('/')}/{port}{parsed.path}" + (f"?{parsed.query}" if parsed.query else "")

    codespace_name = os.getenv("CODESPACE_NAME")
    if codespace_name:
        forwarding_domain = os.getenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev")
        return f"https://{codespace_name}-{port}.{forwarding_domain}{parsed.path}" + (f"?{parsed.query}" if parsed.query else "")

    return url


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

    try:
        yield
    finally:
        _cleanup()


mcp = FastMCP(
    "Panel Live Server",
    instructions=(
        "Panel Live Server executes Python code snippets and renders the resulting "
        "visualizations as live, interactive web pages. Use the `show` tool to display "
        "plots, dashboards, and data apps.\n\n"
        "RECOMMENDED WORKFLOW\n"
        "For simple, well-known code: call `show` directly — it validates and renders in "
        "one step.\n"
        "For complex or uncertain code: call `validate(code, method)` first. If it returns "
        '`{"valid": true}`, call `show` (the cached result is reused, no double-validation). '
        'If it returns `{"valid": false}`, fix the issue before calling `show`.\n\n'
        "ERRORS\n"
        "`show` raises `SecurityError` for blocked imports or dangerous patterns — these "
        "require a substantive code rewrite, not a retry. "
        "`show` raises `ValidationError` for syntax errors, missing packages, or missing "
        "Panel extension declarations — fix the reported issue and try again."
    ),
    lifespan=app_lifespan,
)


# --- Resources ---


@mcp.resource(
    SHOW_RESOURCE_URI,
    app=AppConfig(
        csp=ResourceCSP(
            resource_domains=[
                "'unsafe-inline'",
                "https://unpkg.com",
            ],
            frame_domains=[
                "http://localhost",
                "http://127.0.0.1",
                "https://localhost",
                "https://127.0.0.1",
                "https://*.app.github.dev",
                "https://*.github.dev",
            ],
        )
    ),
)
def show_view() -> str:
    """Return the HTML resource used by the show MCP App."""
    return SHOW_TEMPLATE_PATH.read_text(encoding="utf-8")


# --- Tools ---


@mcp.tool(name="list_packages")
async def list_packages(ctx: Context | None = None) -> list[dict[str, str]]:
    """List all Python packages installed in the server environment.

    Use this tool to discover what libraries are available before writing
    visualization code, so you can import only packages that are installed.

    Returns
    -------
    list[dict[str, str]]
        Sorted list of ``{"name": ..., "version": ...}`` dicts, one per
        installed distribution.

    Examples
    --------
    >>> packages()
    [{"name": "hvplot", "version": "0.11.0"}, ...]
    """
    from importlib.metadata import distributions

    pkgs = sorted(
        ({"name": dist.metadata["Name"], "version": dist.metadata["Version"]} for dist in distributions()),
        key=lambda d: d["name"].lower().replace("-", "_"),
    )
    return pkgs


@mcp.tool(name="validate")
async def validate(
    code: str,
    method: Literal["jupyter", "panel"] = "jupyter",
    ctx: Context | None = None,
) -> str:
    """Validate Python visualization code before calling show().

    Runs four static checks in order and returns a structured result without
    creating a visualization or contacting the Panel server.

    Checks performed:
    1. Syntax — ``ast.parse``
    2. Security — ruff security rules + blocked-import list
    3. Package availability — all imports must be installed in this environment
    4. Panel extensions — declared via ``pn.extension()`` (``panel`` method only;
       ``jupyter`` method auto-injects extensions so no declaration is needed)

    Parameters
    ----------
    code : str
        Python code to validate.
    method : {"jupyter", "panel"}, default "jupyter"
        Execution method — same as the ``method`` parameter of ``show``.

    Returns
    -------
    str
        JSON string: ``{"valid": true}`` on success, or
        ``{"valid": false, "layer": "syntax"|"security"|"packages"|"extensions",
        "message": "..."}`` describing the first failing check.
    """
    result = _run_validation(code, method)
    return json.dumps(result)


@mcp.tool(name="show", app=AppConfig(resource_uri=SHOW_RESOURCE_URI))
async def show(
    code: str,
    name: str = "",
    description: str = "",
    method: Literal["jupyter", "panel"] = "jupyter",
    zoom: int = 100,
    ctx: Context | None = None,
) -> str:
    """Display Python code as a live, interactive visualization.

    Validates and renders code in one step — no prior ``validate()`` call required.
    If you are unsure whether the code is correct, call ``validate(code, method)`` first;
    ``show`` will reuse the cached result and skip re-validation.

    Executes Python code and renders the result in a Panel web interface.
    Always call this tool when the user asks to show, display, plot, or visualize anything.

    IMPORTANT — always provide a short `name` (e.g. "Temperature chart") so the
    visualization can be identified in the feed. The `description` is optional but helpful.

    IMPORTANT — after calling this tool, always present the returned `url` to the user
    as a clickable Markdown link, e.g.: [Open visualization](https://...)

    Parameters
    ----------
    code : str
        Python code to execute.
        For "jupyter" method: the LAST expression is displayed. It must be at column 0
        (fully dedented — no leading whitespace or indentation).
        For "panel" method: call .servable() on the objects you want displayed.
    name : str, optional
        Short descriptive name shown in the visualization feed (e.g. "Sales chart 2024").
        Always provide this — unnamed visualizations are hard to track.
    description : str, optional
        One-sentence description of what the visualization shows.
    method : {"jupyter", "panel"}, default "jupyter"
        Execution mode:
        - "jupyter": displays the last expression's result. Use for standard plots,
          dataframes, and objects that do NOT import panel directly.
        - "panel": displays objects marked `.servable()`. Use when the code imports
          and uses Panel to build dashboards, apps, or complex layouts.
    zoom : {100, 75, 50, 25}, default 100
        Initial zoom level for the visualization preview.
        Choose based on how much content the visualization contains:
        - 100: simple plots, single charts, dataframes, small widgets — fits naturally.
        - 75: multi-panel layouts, apps with a sidebar, moderate dashboards.
        - 50: full-page template apps (FastListTemplate, MaterialTemplate, etc.)
          with header + sidebar + main area.
        - 25: very large or wide apps designed for big screens; use when 50 still
          feels cramped in the preview pane.

    Returns
    -------
    str
        JSON payload for MCP App rendering, including the visualization URL.
    """
    global _manager, _client

    # Clamp zoom to nearest valid level
    _valid_zooms = [25, 50, 75, 100]
    zoom = min(_valid_zooms, key=lambda z: abs(z - zoom))

    # Run static validation (cached — reuses result from a preceding validate() call).
    validation = _run_validation(code, method)
    if not validation["valid"]:
        layer = validation.get("layer", "")
        message = validation.get("message", "Validation failed.")
        if layer == "security":
            raise SecurityError(message)
        elif layer == "syntax":
            raise ValidationError(f"[syntax] {message}")
        elif layer == "packages":
            raise ValidationError(f"[packages] {message}")
        elif layer == "extensions":
            raise ValidationError(f"[extensions] {message}\n" "Add the missing pn.extension(...) call to your code.")
        else:
            raise ValidationError(message)

    if not _client:
        config = get_config()
        raise ToolError(f"Panel Live Server is not running. " f"Restart the MCP server. Ensure port {config.port} is not already in use.")

    # Check health with restart logic
    if not _client.is_healthy():
        if ctx:
            await ctx.info("Panel Live Server is not healthy, attempting restart...")

        if _manager and _manager.restart():
            _client.close()
            _client = DisplayClient(base_url=_manager.get_base_url())
        else:
            config = get_config()
            raise ToolError(f"Panel Live Server is not healthy and failed to restart. " f"Kill any process on port {config.port} and restart the MCP server.")

    # Send request to Panel server
    try:
        response = _client.create_snippet(
            code=code,
            name=name,
            description=description,
            method=method,
        )
        url = _externalize_url(response.get("url", ""))

        payload: dict[str, str | int] = {
            "tool": "show",
            "name": name,
            "description": description,
            "method": method,
            "zoom": zoom,
            "code": code,
            "url": url,
        }

        if error_message := response.get("error_message", None):
            # Runtime error detected at storage time — raise so the LLM gets a
            # clear text error instead of a blank App pane.
            raise ToolError(f"Visualization created but failed at runtime:\n{error_message}\n" "Fix the code and try again.")

        payload["status"] = "success"
        payload["message"] = "Visualization created successfully."
        return json.dumps(payload)

    except SecurityError:
        raise
    except ValidationError:
        raise
    except (SyntaxError, ExtensionError) as e:
        # Defensive fallback: _run_validation() should have caught these already.
        raise ValidationError(str(e)) from e

    except ValueError as e:
        raise ValidationError(
            f"[packages] {e}\nDo NOT install packages or change the environment. "
            "Call list_packages to see what is available, then rewrite using an installed library."
        ) from e

    except Exception as e:
        logger.exception(f"Error creating visualization: {e}")
        if ctx:
            await ctx.error(f"Failed to create visualization: {e}")
        raise ToolError(f"Failed to create visualization: {e!s}. " "Check that the Panel server is running and the code is valid Python.") from e
