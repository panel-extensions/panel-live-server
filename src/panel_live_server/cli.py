"""CLI for Panel Live Server."""

import logging
from typing import Annotated

import typer

from panel_live_server import __version__
from panel_live_server.config import get_config

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
    port: int = typer.Option(
        5077,
        "--port",
        "-p",
        help="Port number to run the server on.",
        envvar="PANEL_LIVE_SERVER_PORT",
        show_default=True,
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
        help="Open the server in a browser.",
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
    and visualizing the results.
    """
    import os

    if verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    if db_path:
        os.environ["PANEL_LIVE_SERVER_DB_PATH"] = db_path

    from panel_live_server.app import main as app_main

    app_main(address=host, port=port, show=show)


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
        help="Host for HTTP transport.",
        envvar="PANEL_LIVE_SERVER_MCP_HOST",
    ),
    port: int = typer.Option(
        8001,
        "--port",
        "-p",
        help="Port for HTTP transport.",
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

    The MCP server provides the `show` tool for executing and displaying
    Python visualizations. The Panel server subprocess starts automatically.
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
        typer.echo(f"Unknown transport: {transport}. Use stdio, http, or sse.")
        raise typer.Exit(1)


def main() -> None:
    """Entry point for the pls command."""
    app()


if __name__ == "__main__":
    main()
