"""CLI for Panel Live Server."""

import errno
import json
import logging
import os
import sys
from importlib.metadata import distributions
from pathlib import Path
from typing import Annotated

# On Windows, conda/pixi environments require Library/bin and DLLs on PATH so
# that native extensions (numpy, panel, etc.) can find their DLLs at import
# time. MCP clients that launch pls directly (not via `pixi run`) don't
# activate the environment, so we fix it up here before any heavy imports.
if sys.platform == "win32":
    from panel_live_server.utils import prepend_env_dll_paths

    prepend_env_dll_paths(os.environ)

import requests
import typer

from panel_live_server import __version__
from panel_live_server.config import default_panel_port
from panel_live_server.config import reset_config
from panel_live_server.install import SERVER_NAME
from panel_live_server.install import InstallError
from panel_live_server.install import antigravity_config_path
from panel_live_server.install import claude_desktop_config_path
from panel_live_server.install import cline_config_path
from panel_live_server.install import codex_config_path
from panel_live_server.install import copilot_config_path
from panel_live_server.install import cursor_config_path
from panel_live_server.install import gemini_cli_config_path
from panel_live_server.install import jetbrains_config_path
from panel_live_server.install import kilo_code_config_path
from panel_live_server.install import kiro_config_path
from panel_live_server.install import merge_codex_server
from panel_live_server.install import merge_kilo_code_server
from panel_live_server.install import merge_mcp_server
from panel_live_server.install import merge_mistral_vibe_server
from panel_live_server.install import mistral_vibe_config_path
from panel_live_server.install import register_with_claude_code
from panel_live_server.install import resolve_pls_command
from panel_live_server.install import vscode_config_path
from panel_live_server.install import windsurf_config_path
from panel_live_server.prompts import render_instructions
from panel_live_server.screenshot import install_browser as _install_browser

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

install_app = typer.Typer(help="Register panel-live-server with an AI client's MCP configuration.")
app.add_typer(install_app, name="install")


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
    if verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    if port is None:
        port = default_panel_port()

    # Set env vars before config is loaded so get_config() picks them up
    os.environ["PANEL_LIVE_SERVER_PORT"] = str(port)
    os.environ["PANEL_LIVE_SERVER_HOST"] = host
    if db_path:
        os.environ["PANEL_LIVE_SERVER_DB_PATH"] = db_path

    # Reset the cached config singleton so it re-reads the env vars we just set
    reset_config()

    # noqa: app pulls in panel, ~487 ms that only `pls serve` needs.
    from panel_live_server.app import main as app_main  # noqa: PLC0415

    try:
        app_main(address=host, port=port, show=show)
    except OSError as exc:
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
    prompts: str = typer.Option(
        "",
        "--prompts",
        help="Path to a JSON file overriding named prompt sections (e.g. library_selection). Sections you omit keep their built-in text.",
        envvar="PANEL_LIVE_SERVER_PROMPTS_FILE",
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

    if prompts:
        os.environ["PANEL_LIVE_SERVER_PROMPTS_FILE"] = prompts

    # noqa: server pulls in panel, ~930 ms that every other command would pay.
    from panel_live_server.server import mcp as mcp_server  # noqa: PLC0415

    # server.py renders at import time, so re-render here (~1 ms) or an earlier import silently wins.
    mcp_server.instructions = render_instructions()

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


_COMMAND_OPTION = typer.Option(
    "",
    "--command",
    help="Path to the `pls` executable to register. Defaults to the `pls` running this command.",
)
_PROMPTS_OPTION = typer.Option(
    "",
    "--prompts",
    help="Register `pls mcp --prompts <file>`, pointing at prompt-section overrides. Omit to keep whatever the existing entry already passed.",
)
_CONFIG_PATH_OPTION = typer.Option(
    "",
    "--config-path",
    help="Write to this config file instead of the client's usual location.",
)


def _register_in_json_config(
    *,
    command: str,
    prompts: str,
    config_path: str,
    default_path,
    servers_key: str,
    entry_type: str,
    restart_hint: str,
    extra_fields: dict | None = None,
) -> None:
    """Shared body for the clients configured by a JSON file on disk."""
    try:
        pls_command = command or resolve_pls_command()
        path = Path(config_path).expanduser() if config_path else default_path()
        args = ["mcp", "--prompts", str(Path(prompts).expanduser())] if prompts else ["mcp"]
        already_installed, entry = merge_mcp_server(
            path,
            pls_command,
            args,
            servers_key=servers_key,
            entry_type=entry_type,
            extra_fields=extra_fields,
        )
    except InstallError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    if already_installed:
        typer.echo(f"{SERVER_NAME} is already registered in {path}")
    else:
        typer.echo(f"Registered {SERVER_NAME} in {path}")
    typer.echo("")
    typer.echo(json.dumps({servers_key: {SERVER_NAME: entry}}, indent=2))
    typer.echo("")
    typer.echo(restart_hint)


@install_app.command(name="claude")
def install_claude(
    command: str = _COMMAND_OPTION,
    prompts: str = _PROMPTS_OPTION,
    config_path: str = _CONFIG_PATH_OPTION,
) -> None:
    """Register panel-live-server with Claude Desktop.

    Adds (or updates) the `panel-live-server` entry under `mcpServers` in
    Claude Desktop's config file, without touching any other server already
    configured there. Flags that an existing entry passed after `mcp` are
    carried over, so a `--prompts` file set up by hand survives a re-run.
    Restart Claude Desktop afterwards for the change to take effect.
    """
    _register_in_json_config(
        command=command,
        prompts=prompts,
        config_path=config_path,
        default_path=claude_desktop_config_path,
        servers_key="mcpServers",
        entry_type="",
        restart_hint="Restart Claude Desktop for the change to take effect.",
    )


@install_app.command(name="cursor")
def install_cursor(
    command: str = _COMMAND_OPTION,
    prompts: str = _PROMPTS_OPTION,
    config_path: str = _CONFIG_PATH_OPTION,
) -> None:
    """Register panel-live-server with Cursor.

    Writes to `~/.cursor/mcp.json`, leaving any other server there untouched.
    Afterwards open Cursor Settings, MCP, and check for the green dot.
    """
    _register_in_json_config(
        command=command,
        prompts=prompts,
        config_path=config_path,
        default_path=cursor_config_path,
        servers_key="mcpServers",
        entry_type="",
        restart_hint="Open Cursor Settings, MCP, and check for the green dot. Use Agent mode in chat.",
    )


@install_app.command(name="vscode")
def install_vscode(
    command: str = _COMMAND_OPTION,
    prompts: str = _PROMPTS_OPTION,
    config_path: str = _CONFIG_PATH_OPTION,
) -> None:
    """Register panel-live-server with VS Code.

    VS Code reads MCP servers per project, so this writes `.vscode/mcp.json`
    in the directory you run it from, not a file in your home directory.
    """
    _register_in_json_config(
        command=command,
        prompts=prompts,
        config_path=config_path,
        default_path=vscode_config_path,
        servers_key="servers",
        entry_type="stdio",
        restart_hint="Reload the VS Code window for the change to take effect.",
    )


@install_app.command(name="claude-code")
def install_claude_code(
    command: str = _COMMAND_OPTION,
    prompts: str = _PROMPTS_OPTION,
) -> None:
    """Register panel-live-server with Claude Code.

    Claude Code keeps its own MCP registry rather than a config file to edit,
    so this runs `claude mcp add` for you. If the server is already registered,
    remove it first with `claude mcp remove panel-live-server`.
    """
    try:
        pls_command = command or resolve_pls_command()
        args = ["mcp", "--prompts", str(Path(prompts).expanduser())] if prompts else ["mcp"]
        ran = register_with_claude_code(pls_command, args)
    except InstallError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    typer.echo("Registered panel-live-server with Claude Code, via:")
    typer.echo("")
    typer.echo(f"  {ran}")


@install_app.command(name="windsurf")
def install_windsurf(
    command: str = _COMMAND_OPTION,
    prompts: str = _PROMPTS_OPTION,
    config_path: str = _CONFIG_PATH_OPTION,
) -> None:
    """Register panel-live-server with Windsurf.

    Writes to `~/.codeium/windsurf/mcp_config.json`, leaving any other server
    there untouched.
    """
    _register_in_json_config(
        command=command,
        prompts=prompts,
        config_path=config_path,
        default_path=windsurf_config_path,
        servers_key="mcpServers",
        entry_type="",
        restart_hint="Open the MCP servers panel (hammer icon) in Cascade and confirm panel-live-server is running.",
    )


@install_app.command(name="cline")
def install_cline(
    command: str = _COMMAND_OPTION,
    prompts: str = _PROMPTS_OPTION,
    config_path: str = _CONFIG_PATH_OPTION,
) -> None:
    """Register panel-live-server with Cline.

    Cline stores MCP servers in VS Code's per-extension global storage rather
    than `.vscode/mcp.json`, so this is a separate config file from the `vscode`
    command even when both run in the same editor.
    """
    _register_in_json_config(
        command=command,
        prompts=prompts,
        config_path=config_path,
        default_path=cline_config_path,
        servers_key="mcpServers",
        entry_type="",
        restart_hint="Open Cline's MCP Servers panel and confirm panel-live-server is connected.",
    )


@install_app.command(name="jetbrains")
def install_jetbrains(
    command: str = _COMMAND_OPTION,
    prompts: str = _PROMPTS_OPTION,
    config_path: str = _CONFIG_PATH_OPTION,
) -> None:
    """Register panel-live-server with Junie (JetBrains AI Assistant).

    Writes to `~/.junie/mcp/mcp.json`, which applies to every project opened in
    the IDE. Add an `.junie/mcp/mcp.json` in a specific project instead if you
    only want it there.
    """
    _register_in_json_config(
        command=command,
        prompts=prompts,
        config_path=config_path,
        default_path=jetbrains_config_path,
        servers_key="mcpServers",
        entry_type="",
        restart_hint="Restart the IDE, or reopen Junie's MCP settings, for the change to take effect.",
    )


@install_app.command(name="gemini-cli")
def install_gemini_cli(
    command: str = _COMMAND_OPTION,
    prompts: str = _PROMPTS_OPTION,
    config_path: str = _CONFIG_PATH_OPTION,
) -> None:
    """Register panel-live-server with the Gemini CLI.

    Writes to `~/.gemini/settings.json`, leaving every other setting there
    (theme, auth, other servers) untouched.
    """
    _register_in_json_config(
        command=command,
        prompts=prompts,
        config_path=config_path,
        default_path=gemini_cli_config_path,
        servers_key="mcpServers",
        entry_type="",
        restart_hint="Restart the Gemini CLI for the change to take effect.",
    )


@install_app.command(name="antigravity")
def install_antigravity(
    command: str = _COMMAND_OPTION,
    prompts: str = _PROMPTS_OPTION,
    config_path: str = _CONFIG_PATH_OPTION,
) -> None:
    """Register panel-live-server with Google Antigravity.

    Writes to `~/.gemini/config/mcp_config.json`, Antigravity's global MCP
    config. Add an `.agents/mcp_config.json` in a specific project instead if
    you only want it there.
    """
    _register_in_json_config(
        command=command,
        prompts=prompts,
        config_path=config_path,
        default_path=antigravity_config_path,
        servers_key="mcpServers",
        entry_type="",
        restart_hint="Open Manage MCP Servers in Antigravity and confirm panel-live-server is connected.",
    )


@install_app.command(name="kiro")
def install_kiro(
    command: str = _COMMAND_OPTION,
    prompts: str = _PROMPTS_OPTION,
    config_path: str = _CONFIG_PATH_OPTION,
) -> None:
    """Register panel-live-server with Kiro.

    Writes to `~/.kiro/settings/mcp.json`, which applies to every workspace.
    Add a `.kiro/settings/mcp.json` in a specific project instead if you only
    want it there.
    """
    _register_in_json_config(
        command=command,
        prompts=prompts,
        config_path=config_path,
        default_path=kiro_config_path,
        servers_key="mcpServers",
        entry_type="",
        restart_hint="Reload Kiro, or open the MCP panel, for the change to take effect.",
    )


@install_app.command(name="copilot")
def install_copilot(
    command: str = _COMMAND_OPTION,
    prompts: str = _PROMPTS_OPTION,
    config_path: str = _CONFIG_PATH_OPTION,
) -> None:
    """Register panel-live-server with the GitHub Copilot CLI.

    Writes to `~/.copilot/mcp-config.json` (or `$COPILOT_HOME/mcp-config.json`
    if set). This is the standalone `copilot` CLI, not VS Code's Copilot Chat,
    which already reads `.vscode/mcp.json` via the `vscode` command.
    """
    _register_in_json_config(
        command=command,
        prompts=prompts,
        config_path=config_path,
        default_path=copilot_config_path,
        servers_key="mcpServers",
        entry_type="local",
        extra_fields={"tools": ["*"]},
        restart_hint="Run `/mcp show` in the Copilot CLI to confirm panel-live-server is connected.",
    )


@install_app.command(name="kilo-code")
def install_kilo_code(
    command: str = _COMMAND_OPTION,
    prompts: str = _PROMPTS_OPTION,
    config_path: str = _CONFIG_PATH_OPTION,
) -> None:
    """Register panel-live-server with Kilo Code.

    Writes to `~/.config/kilo/kilo.jsonc`. Kilo Code packs the command and its
    arguments into one array rather than separate `command`/`args` fields, so
    the entry looks different from every other client here.
    """
    try:
        pls_command = command or resolve_pls_command()
        path = Path(config_path).expanduser() if config_path else kilo_code_config_path()
        args = ["mcp", "--prompts", str(Path(prompts).expanduser())] if prompts else ["mcp"]
        already_installed, entry = merge_kilo_code_server(path, pls_command, args)
    except InstallError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    if already_installed:
        typer.echo(f"{SERVER_NAME} is already registered in {path}")
    else:
        typer.echo(f"Registered {SERVER_NAME} in {path}")
    typer.echo("")
    typer.echo(json.dumps({"mcp": {SERVER_NAME: entry}}, indent=2))
    typer.echo("")
    typer.echo("Reload the window for the change to take effect.")


@install_app.command(name="codex")
def install_codex(
    command: str = _COMMAND_OPTION,
    prompts: str = _PROMPTS_OPTION,
    config_path: str = _CONFIG_PATH_OPTION,
) -> None:
    """Register panel-live-server with the OpenAI Codex CLI.

    Writes to `~/.codex/config.toml`, leaving every other setting and server
    there untouched.
    """
    try:
        pls_command = command or resolve_pls_command()
        path = Path(config_path).expanduser() if config_path else codex_config_path()
        args = ["mcp", "--prompts", str(Path(prompts).expanduser())] if prompts else ["mcp"]
        already_installed, entry = merge_codex_server(path, pls_command, args)
    except InstallError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    if already_installed:
        typer.echo(f"{SERVER_NAME} is already registered in {path}")
    else:
        typer.echo(f"Registered {SERVER_NAME} in {path}")
    typer.echo("")
    typer.echo(f"[mcp_servers.{SERVER_NAME}]")
    typer.echo(f'command = "{entry["command"]}"')
    typer.echo(f"args = {json.dumps(entry['args'])}")
    typer.echo("")
    typer.echo("Restart the Codex CLI for the change to take effect.")


@install_app.command(name="mistral-vibe")
def install_mistral_vibe(
    command: str = _COMMAND_OPTION,
    prompts: str = _PROMPTS_OPTION,
    config_path: str = _CONFIG_PATH_OPTION,
) -> None:
    """Register panel-live-server with Mistral Vibe.

    Writes to `~/.vibe/config.toml`, leaving every other setting and server
    there untouched.
    """
    try:
        pls_command = command or resolve_pls_command()
        path = Path(config_path).expanduser() if config_path else mistral_vibe_config_path()
        args = ["mcp", "--prompts", str(Path(prompts).expanduser())] if prompts else ["mcp"]
        already_installed, entry = merge_mistral_vibe_server(path, pls_command, args)
    except InstallError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    if already_installed:
        typer.echo(f"{SERVER_NAME} is already registered in {path}")
    else:
        typer.echo(f"Registered {SERVER_NAME} in {path}")
    typer.echo("")
    typer.echo("[[mcp_servers]]")
    typer.echo(f'name = "{entry["name"]}"')
    typer.echo(f'transport = "{entry["transport"]}"')
    typer.echo(f'command = "{entry["command"]}"')
    typer.echo(f"args = {json.dumps(entry['args'])}")
    typer.echo("")
    typer.echo("Restart Vibe for the change to take effect.")


@app.command(name="install-browser")
def install_browser() -> None:
    """Download the Chromium browser the `screenshot` MCP tool needs.

    Playwright ships its browser binary separately from the Python package, so a
    `pip` or `uv` install does not fetch it automatically. Run this once after
    installing (pixi users get it via `pixi run postinstall`). It lands in the
    same environment that runs `pls`.
    """
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
