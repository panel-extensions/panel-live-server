"""Register panel-live-server with an AI client's MCP configuration."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SERVER_NAME = "panel-live-server"


class InstallError(Exception):
    """Raised when a client's config can't be located, read, or written."""


def claude_desktop_config_path() -> Path:
    """Return Claude Desktop's config file path for the current OS."""
    if sys.platform == "darwin":
        return Path("~/Library/Application Support/Claude/claude_desktop_config.json").expanduser()
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise InstallError("APPDATA is not set, cannot locate Claude Desktop's config.")
        return Path(appdata) / "Claude" / "claude_desktop_config.json"
    return Path("~/.config/Claude/claude_desktop_config.json").expanduser()


def cursor_config_path() -> Path:
    """Return Cursor's MCP config file path."""
    return Path("~/.cursor/mcp.json").expanduser()


def vscode_config_path() -> Path:
    """Return VS Code's MCP config path, relative to the current project.

    Unlike the others this one is per-project, not per-user, so it lands in the
    directory the command is run from.
    """
    return Path(".vscode/mcp.json")


def resolve_pls_command() -> str:
    """Return the absolute path to the `pls` that is running right now.

    Deliberately not ``shutil.which("pls")``: several environments can each hold
    their own ``pls``, and the one first on PATH is often not the one invoked. A
    developer running ``.pixi/envs/default/bin/pls install claude`` means *that*
    ``pls``, editable against their checkout, not a frozen copy in some other
    venv that happens to shadow it. So the interpreter running this command is
    what gets registered, with PATH as the fallback for odd launch setups.
    """
    alongside = Path(sys.executable).parent / ("pls.exe" if sys.platform == "win32" else "pls")
    if alongside.exists():
        return str(alongside)
    found = shutil.which("pls")
    if not found:
        raise InstallError("Could not find `pls`. Pass --command to specify it explicitly.")
    return found


def _merge_args(existing: object, args: list[str]) -> list[str]:
    """Carry flags the user had added by hand over to the new entry.

    A config in the wild rarely holds a bare ``["mcp"]``: people add ``--prompts``
    and friends, and rewriting the entry from scratch would silently drop them.
    So flags already sitting after ``mcp`` are kept, unless this call passes flags
    of its own, in which case the explicit request wins outright.
    """
    if len(args) > 1 or not isinstance(existing, dict):
        return args
    old = existing.get("args")
    if not isinstance(old, list) or len(old) < 2:
        return args
    return args + [a for a in old[1:] if isinstance(a, str)]


def merge_mcp_server(
    config_path: Path,
    command: str,
    args: list[str],
    *,
    servers_key: str = "mcpServers",
    entry_type: str = "",
) -> tuple[bool, dict]:
    """Merge a `panel-live-server` entry into an MCP client's config file.

    Only the `panel-live-server` key is touched; every other server already
    configured is left as-is, as are any extra flags the existing entry passed
    after ``mcp``. Creates the file (and its parent directory) if it does not
    exist yet.

    Clients disagree on the shape: VS Code nests under ``servers`` and wants an
    explicit ``type``, while Claude Desktop and Cursor use ``mcpServers`` without
    one, hence the two keyword arguments.

    Returns whether the file already held this exact entry (nothing changed), and
    the entry itself, so callers can show what a hand-written config would need.
    """
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InstallError(f"{config_path} is not valid JSON: {exc}") from exc
    else:
        data = {}

    servers = data.setdefault(servers_key, {})
    entry: dict = {"type": entry_type} if entry_type else {}
    entry["command"] = command
    entry["args"] = _merge_args(servers.get(SERVER_NAME), args)

    if servers.get(SERVER_NAME) == entry:
        return True, entry

    servers[SERVER_NAME] = entry
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return False, entry


def register_with_claude_code(command: str, args: list[str]) -> str:
    """Register the server with Claude Code, which owns its own config format.

    Claude Code stores MCP servers itself rather than in a file we should edit,
    so this shells out to its CLI instead of merging JSON.
    """
    if not shutil.which("claude"):
        raise InstallError("Could not find the `claude` CLI on PATH. Install Claude Code first, then re-run this.")

    cli_args = ["claude", "mcp", "add", SERVER_NAME, "--", command, *args]
    result = subprocess.run(cli_args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise InstallError(f"`claude mcp add` failed: {detail}")
    return " ".join(cli_args)
