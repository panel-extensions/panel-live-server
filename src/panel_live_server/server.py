"""Panel Live Server - MCP Server.

A standalone MCP server that provides `show` and `show_pyodide` tools
for executing Python code and rendering visualizations via a Panel web server.
"""

import atexit
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from typing import Optional
from urllib.parse import urlparse

from fastmcp import Context
from fastmcp import FastMCP
from fastmcp.server.apps import AppConfig
from fastmcp.server.apps import ResourceCSP

from panel_live_server.client import DisplayClient
from panel_live_server.config import get_config
from panel_live_server.manager import PanelServerManager

logger = logging.getLogger(__name__)

SHOW_RESOURCE_URI = "ui://panel-live-server/show.html"
SHOW_TEMPLATE_PATH = Path(__file__).parent / "templates" / "show.html"
SHOW_PYODIDE_RESOURCE_URI = "ui://panel-live-server/show-pyodide.html"
SHOW_PYODIDE_TEMPLATE_PATH = Path(__file__).parent / "templates" / "show_pyodide.html"

# Global instances
_manager: Optional[PanelServerManager] = None
_client: Optional[DisplayClient] = None


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


def _start_panel_server() -> tuple[Optional[PanelServerManager], Optional[DisplayClient]]:
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
        logger.info("Panel Live Server started successfully")
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
        "plots, dashboards, and data apps. Use `show_pyodide` for client-side rendering."
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


@mcp.resource(
    SHOW_PYODIDE_RESOURCE_URI,
    app=AppConfig(
        csp=ResourceCSP(
            resource_domains=[
                "'unsafe-inline'",
                "'unsafe-eval'",
                "'wasm-unsafe-eval'",
                "blob:",
                "data:",
                "https://unpkg.com",
                "https://panel-extensions.github.io",
                "https://cdn.holoviz.org",
                "https://cdn.jsdelivr.net",
                "https://cdn.plot.ly",
                "https://pyodide-cdn2.iodide.io",
                "https://pypi.org",
                "https://files.pythonhosted.org",
                "https://cdn.bokeh.org",
                "https://raw.githubusercontent.com",
            ],
            connect_domains=[
                "https://panel-extensions.github.io",
                "https://cdn.holoviz.org",
                "https://cdn.jsdelivr.net",
                "https://cdn.plot.ly",
                "https://pyodide-cdn2.iodide.io",
                "https://pypi.org",
                "https://files.pythonhosted.org",
                "https://cdn.bokeh.org",
                "https://raw.githubusercontent.com",
            ],
        )
    ),
)
def show_pyodide_view() -> str:
    """Return the HTML resource used by the show_pyodide MCP App."""
    return SHOW_PYODIDE_TEMPLATE_PATH.read_text(encoding="utf-8")


# --- Tools ---

@mcp.tool(name="show", app=AppConfig(resource_uri=SHOW_RESOURCE_URI))
async def show(
    code: str,
    name: str = "",
    description: str = "",
    method: Literal["jupyter", "panel"] = "jupyter",
    ctx: Context | None = None,
) -> str:
    """Display Python code visualization in a browser.

    This tool executes Python code and renders it in a Panel web interface,
    returning a URL where you can view the output. The code is validated
    before execution and any errors are reported immediately.

    Use this tool whenever the user asks to show, display, visualize data, plots, dashboards, and other Python objects.

    Parameters
    ----------
    code : str
        The Python code to execute. For "jupyter" method, the last line is displayed.
        For "panel" method, objects marked .servable() are displayed.
    name : str, optional
        A name for the visualization (displayed in admin/feed views)
    description : str, optional
        A short description of the visualization
    method : {"jupyter", "panel"}, default "jupyter"
        Execution mode:
        - "jupyter": Execute code and display the last expression's result. The last expression must be dedented fully.
            DO use this for standard data visualizations like plots, dataframes, etc. that do not import and use Panel directly.
        - "panel": Execute code and display Panel objects marked .servable()
            DO use this for code that imports and uses Panel to create dashboards, apps, and complex layouts.

    Returns
    -------
    str
        JSON payload as text for MCP App rendering, including a visualization URL.
    """
    global _manager, _client

    if not _client:
        return "Error: Panel Live Server is not running. Check logs for startup errors."

    # Check health with restart logic
    if not _client.is_healthy():
        if ctx:
            await ctx.info("Panel Live Server is not healthy, attempting restart...")

        if _manager and _manager.restart():
            _client.close()
            _client = DisplayClient(base_url=_manager.get_base_url())
        else:
            return "Error: Panel Live Server is not healthy and failed to restart."

    # Send request to Panel server
    try:
        response = _client.create_snippet(
            code=code,
            name=name,
            description=description,
            method=method,
        )
        url = _externalize_url(response.get("url", ""))

        payload: dict[str, str] = {
            "tool": "show",
            "name": name,
            "description": description,
            "method": method,
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

    except Exception as e:
        logger.exception(f"Error creating visualization: {e}")

        if ctx:
            await ctx.error(f"Failed to create visualization: {e}")

        return f"Error: Failed to create visualization: {str(e)}"


@mcp.tool(name="show_pyodide", app=AppConfig(resource_uri=SHOW_PYODIDE_RESOURCE_URI))
async def show_pyodide(
    code: str,
    name: str = "",
    description: str = "",
    ctx: Context | None = None,
) -> str:
    """Display Python code as a panel-live Pyodide app in MCP Apps-capable clients.

    This tool is intended for browser/Pyodide rendering paths where direct
    server execution should be avoided. It returns a payload consumed
    by the linked MCP App resource.

    Parameters
    ----------
    code : str
        Python code to run inside panel-live/Pyodide runtime.
    name : str, optional
        Optional display name used by the app UI.
    description : str, optional
        Optional description shown in the app UI.
    ctx : Context | None, optional
        FastMCP execution context.

    Returns
    -------
    str
        JSON payload as text for the MCP App resource.
    """
    if not code.strip():
        return json.dumps({"error": "Code is required for show_pyodide."})

    payload = {
        "tool": "show_pyodide",
        "name": name,
        "description": description,
        "code": code,
        "runtime": "panel-live-pyodide",
    }

    if ctx:
        await ctx.info("Prepared show_pyodide payload for MCP App rendering.")

    return json.dumps(payload)
