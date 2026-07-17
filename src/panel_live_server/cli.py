"""CLI for Panel Live Server."""

import logging
import os
import sys
from typing import Annotated

# On Windows, conda/pixi environments require Library/bin and DLLs on PATH so
# that native extensions (numpy, panel, etc.) can find their DLLs at import
# time. MCP clients that launch pls directly (not via `pixi run`) don't
# activate the environment, so we fix it up here before any heavy imports.
if sys.platform == "win32":
    from panel_live_server.utils import prepend_env_dll_paths

    prepend_env_dll_paths(os.environ)

import typer

from panel_live_server import __version__

logger = logging.getLogger(__name__)


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        typer.echo(f"panel-live-server {__version__}")
        raise typer.Exit()


app = typer.Typer(
    name="pls",
    help="Panel Live Server - Execute and visualize Python code snippets.",
    add_completion=False,
)

list_app = typer.Typer(help="List resources (packages, etc.).")
app.add_typer(list_app, name="list")


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option("--version", "-V", callback=version_callback, is_eager=True, help="Show version and exit."),
    ] = False,
) -> None:
    """Panel Live Server - Execute and visualize Python code snippets."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@app.command()
def serve(
    port: int | None = typer.Option(
        None,
        "--port",
        "-p",
        help="Port to run the Panel server on. Defaults to a per-environment port derived from the interpreter.",
        envvar="PANEL_LIVE_SERVER_PORT",
    ),
    host: str = typer.Option(
        "localhost",
        "--host",
        "-H",
        help="Host address to bind to.",
        envvar="PANEL_LIVE_SERVER_HOST",
        show_default=True,
    ),
    db_path: str | None = typer.Option(
        None,
        "--db-path",
        help="Path to the SQLite database file.",
        envvar="PANEL_LIVE_SERVER_DB_PATH",
    ),
    show: bool = typer.Option(
        False,
        "--show",
        help="Open the server in a browser after starting.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose logging.",
    ),
) -> None:
    """Start the Panel Live Server directly.

    The server provides a web interface for executing Python code snippets
    and visualizing the results. Visit http://<host>:<port>/feed to see
    visualizations as they are created.

    Note: `pls serve` and `pls mcp` launched from the same environment resolve to
    the same per-environment default port, so a browser opened here shows the
    visualizations the MCP server renders. Set PANEL_LIVE_SERVER_PORT (or --port)
    to override.
    """
    import os

    if verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    from panel_live_server.config import default_panel_port
    from panel_live_server.config import reset_config

    if port is None:
        port = default_panel_port()

    # Set env vars before config is loaded so get_config() picks them up
    os.environ["PANEL_LIVE_SERVER_PORT"] = str(port)
    os.environ["PANEL_LIVE_SERVER_HOST"] = host
    if db_path:
        os.environ["PANEL_LIVE_SERVER_DB_PATH"] = db_path

    # Reset the cached config singleton so it re-reads the env vars we just set
    reset_config()

    from panel_live_server.app import main as app_main

    try:
        app_main(address=host, port=port, show=show)
    except OSError as exc:
        import errno

        import requests

        if exc.errno != errno.EADDRINUSE:
            raise
        url = f"http://{host}:{port}/api/health"
        try:
            resp = requests.get(url, timeout=2)
            if resp.status_code == 200:
                typer.echo(f"Panel Live Server is already running at http://{host}:{port}")
                typer.echo("  Run `pls status` for details.")
                raise typer.Exit(0)
        except requests.ConnectionError:
            pass
        typer.echo(f"Error: port {port} is already in use by another process.", err=True)
        typer.echo(f"  Try: pls serve --port {port + 1}", err=True)
        raise typer.Exit(1) from None


@app.command()
def mcp(
    transport: str = typer.Option(
        "stdio",
        "--transport",
        "-t",
        help="MCP transport: stdio, http, or sse.",
        envvar="PANEL_LIVE_SERVER_TRANSPORT",
    ),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Host for HTTP/SSE transport.",
        envvar="PANEL_LIVE_SERVER_MCP_HOST",
    ),
    port: int = typer.Option(
        8001,
        "--port",
        "-p",
        help="Port for HTTP/SSE transport.",
        envvar="PANEL_LIVE_SERVER_MCP_PORT",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose logging.",
    ),
) -> None:
    """Start as an MCP server for AI assistants.

    The MCP server exposes the `show` tool for executing and displaying
    Python visualizations. A Panel visualization server starts automatically
    on a per-environment port (override with PANEL_LIVE_SERVER_PORT) — run
    `pls status` to see the address, then visit its /feed in a browser to
    watch visualizations appear in real time.

    Note: the --port flag here controls the MCP HTTP/SSE listener, NOT the
    Panel visualization server port. For stdio transport, --port is unused.
    """
    if verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    from panel_live_server.server import mcp as mcp_server

    if transport == "stdio":
        mcp_server.run(transport="stdio")
    elif transport == "http":
        mcp_server.run(transport="streamable-http", host=host, port=port)
    elif transport == "sse":
        mcp_server.run(transport="sse", host=host, port=port)
    else:
        typer.echo(f"Unknown transport: {transport!r}. Choose from: stdio, http, sse.")
        raise typer.Exit(1)


@app.command()
def status(
    port: int | None = typer.Option(
        None,
        "--port",
        "-p",
        help="Port to check. Defaults to the per-environment port derived from the interpreter.",
        envvar="PANEL_LIVE_SERVER_PORT",
    ),
    host: str = typer.Option(
        "localhost",
        "--host",
        "-H",
        help="Host to check.",
        envvar="PANEL_LIVE_SERVER_HOST",
        show_default=True,
    ),
) -> None:
    """Check whether the Panel server is running.

    Queries the health endpoint and reports the server status.
    """
    import requests

    from panel_live_server.config import default_panel_port

    if port is None:
        port = default_panel_port()

    url = f"http://{host}:{port}/api/health"
    try:
        resp = requests.get(url, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            typer.echo(f"Running  http://{host}:{port}/feed  (healthy at {data.get('timestamp', '?')})")
        else:
            typer.echo(f"Unhealthy  http://{host}:{port}  (status {resp.status_code})")
            raise typer.Exit(1)
    except requests.ConnectionError:
        typer.echo(f"Not running  (nothing on {host}:{port})")
        raise typer.Exit(1) from None
    except requests.Timeout:
        typer.echo(f"Timeout  (no response from {host}:{port} within 3 s)")
        raise typer.Exit(1) from None


@list_app.command(name="packages")
def list_packages(
    filter: str = typer.Argument(
        "",
        help="Optional substring to filter package names (case-insensitive).",
        show_default=False,
    ),
) -> None:
    """List all Python packages installed in the current environment.

    Optionally filter by a substring, e.g. ``pls list packages panel`` to show
    only packages whose name contains "panel".
    """
    from importlib.metadata import distributions

    pkgs = sorted(
        ((dist.metadata["Name"], dist.metadata["Version"]) for dist in distributions()),
        key=lambda t: t[0].lower().replace("-", "_"),
    )

    if filter:
        pkgs = [(name, ver) for name, ver in pkgs if filter.lower() in name.lower()]

    if not pkgs:
        typer.echo("No packages found.")
        return

    name_width = max(len(name) for name, _ in pkgs)
    for name, version in pkgs:
        typer.echo(f"{name:<{name_width}}  {version}")


@app.command(name="install-browser")
def install_browser() -> None:
    """Download the Chromium browser the `screenshot` MCP tool needs.

    Playwright ships its browser binary separately from the Python package, so a
    `pip` or `uv` install does not fetch it automatically. Run this once after
    installing (pixi users get it via `pixi run postinstall`). It lands in the
    same environment that runs `pls`.
    """
    from panel_live_server.screenshot import install_browser as _install_browser

    typer.echo("Installing Chromium for the screenshot tool (one-time)...")
    code = _install_browser()
    if code == 0:
        typer.echo("Done — the screenshot tool is ready.")
    else:
        typer.echo(
            "Browser install failed. Try manually: python -m playwright install chromium",
            err=True,
        )
        raise typer.Exit(code)


def main() -> None:
    """Entry point for the pls command."""
    app()


if __name__ == "__main__":
    main()
