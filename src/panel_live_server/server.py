"""Panel Live Server - MCP Server.

A standalone MCP server that provides the `show` tool
for executing Python code and rendering visualizations via a Panel web server.
"""

import atexit
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastmcp import Context
from fastmcp import FastMCP
from fastmcp.server.apps import AppConfig
from fastmcp.server.apps import ResourceCSP

from panel_live_server.client import DisplayClient
from panel_live_server.config import get_config
from panel_live_server.manager import PanelServerManager
from panel_live_server.utils import ExtensionError
from panel_live_server.validation import SecurityError

logger = logging.getLogger(__name__)

SHOW_RESOURCE_URI = "ui://panel-live-server/show.html"
SHOW_TEMPLATE_PATH = Path(__file__).parent / "templates" / "show.html"

# Global instances
_manager: PanelServerManager | None = None
_client: DisplayClient | None = None


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


def _cleanup():
    """Cleanup Panel server on exit."""
    global _manager, _client
    if _client:
        logger.info("Cleaning up Panel Live Server client")
        _client.close()
        _client = None
    if _manager:
        logger.info("Stopping Panel Live Server")
        _manager.stop()
        _manager = None


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
        "plots, dashboards, and data apps."
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

    if not _client:
        config = get_config()
        return json.dumps(
            {
                "status": "error",
                "message": "Panel Live Server is not running. Check logs for startup errors.",
                "recovery": f"Restart the MCP server. Ensure port {config.port} is not already in use.",
            }
        )

    # Check health with restart logic
    if not _client.is_healthy():
        if ctx:
            await ctx.info("Panel Live Server is not healthy, attempting restart...")

        if _manager and _manager.restart():
            _client.close()
            _client = DisplayClient(base_url=_manager.get_base_url())
        else:
            config = get_config()
            return json.dumps(
                {
                    "status": "error",
                    "message": "Panel Live Server is not healthy and failed to restart.",
                    "recovery": f"Kill any process on port {config.port} and restart the MCP server.",
                }
            )

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
            payload["status"] = "warning"
            payload["message"] = "Visualization created with errors."
            payload["error_message"] = str(error_message)
            return json.dumps(payload)

        payload["status"] = "success"
        payload["message"] = "Visualization created successfully."
        return json.dumps(payload)

    except SyntaxError as e:
        return json.dumps(
            {
                "status": "error",
                "message": f"Syntax error: {e}",
                "recovery": "Fix the syntax error and try again.",
            }
        )

    except SecurityError as e:
        return json.dumps(
            {
                "status": "error",
                "message": f"Security violation:\n{e}",
                "recovery": "Rewrite the code without the flagged pattern.",
            }
        )

    except ExtensionError as e:
        return json.dumps(
            {
                "status": "error",
                "message": str(e),
                "recovery": "Add the missing pn.extension(...) call to your code.",
            }
        )

    except ValueError as e:
        return json.dumps(
            {
                "status": "error",
                "message": str(e),
                "recovery": "Use the list_packages tool to check what is available, then adjust your code.",
            }
        )

    except Exception as e:
        logger.exception(f"Error creating visualization: {e}")

        if ctx:
            await ctx.error(f"Failed to create visualization: {e}")

        return json.dumps(
            {
                "status": "error",
                "message": f"Failed to create visualization: {e!s}",
                "recovery": "Check that the Panel server is running and the code is valid Python.",
            }
        )
