"""Register panel-live-server with an AI client's MCP configuration."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import tomlkit
from tomlkit.exceptions import TOMLKitError

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


def windsurf_config_path() -> Path:
    """Return Windsurf's MCP config file path."""
    return Path("~/.codeium/windsurf/mcp_config.json").expanduser()


def cline_config_path() -> Path:
    """Return Cline's MCP config file path for the current OS.

    Cline is a VS Code extension, so it stores its config in VS Code's own
    per-extension global storage rather than a location of its own.
    """
    if sys.platform == "darwin":
        return Path("~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json").expanduser()
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise InstallError("APPDATA is not set, cannot locate Cline's config.")
        return Path(appdata) / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json"
    return Path("~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json").expanduser()


def jetbrains_config_path() -> Path:
    """Return Junie's (JetBrains AI Assistant) global MCP config file path."""
    return Path("~/.junie/mcp/mcp.json").expanduser()


def gemini_cli_config_path() -> Path:
    """Return the Gemini CLI's global settings file path."""
    return Path("~/.gemini/settings.json").expanduser()


def antigravity_config_path() -> Path:
    """Return Google Antigravity's global MCP config file path."""
    return Path("~/.gemini/config/mcp_config.json").expanduser()


def kiro_config_path() -> Path:
    """Return Kiro's global MCP config file path."""
    return Path("~/.kiro/settings/mcp.json").expanduser()


def copilot_config_path() -> Path:
    """Return the GitHub Copilot CLI's MCP config file path.

    Respects ``COPILOT_HOME`` the same way Copilot itself does, since that is
    the documented way to relocate it.
    """
    home = os.environ.get("COPILOT_HOME")
    if home:
        return Path(home) / "mcp-config.json"
    return Path("~/.copilot/mcp-config.json").expanduser()


def kilo_code_config_path() -> Path:
    """Return Kilo Code's global config file path."""
    return Path("~/.config/kilo/kilo.jsonc").expanduser()


def codex_config_path() -> Path:
    """Return the Codex CLI's global config file path."""
    return Path("~/.codex/config.toml").expanduser()


def mistral_vibe_config_path() -> Path:
    """Return Mistral Vibe's global config file path."""
    return Path("~/.vibe/config.toml").expanduser()


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
    extra_fields: dict | None = None,
) -> tuple[bool, dict]:
    """Merge a `panel-live-server` entry into an MCP client's config file.

    Only the `panel-live-server` key is touched; every other server already
    configured is left as-is, as are any extra flags the existing entry passed
    after ``mcp``. Creates the file (and its parent directory) if it does not
    exist yet.

    Clients disagree on the shape: VS Code nests under ``servers`` and wants an
    explicit ``type``, while Claude Desktop and Cursor use ``mcpServers`` without
    one, hence the two keyword arguments. ``extra_fields`` covers the rest, e.g.
    GitHub Copilot CLI's ``tools`` list.

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
    if extra_fields:
        entry.update(extra_fields)

    if servers.get(SERVER_NAME) == entry:
        return True, entry

    servers[SERVER_NAME] = entry
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return False, entry


def merge_kilo_code_server(config_path: Path, command: str, args: list[str]) -> tuple[bool, dict]:
    """Merge a `panel-live-server` entry into Kilo Code's config.

    Kilo Code nests servers under ``mcp`` rather than ``mcpServers``, and packs
    the binary and its arguments into one ``command`` array instead of separate
    ``command``/``args`` fields, so it needs its own merge instead of
    ``merge_mcp_server``. The config file is JSONC, but hand-written comments
    are rare before a first install, so this reads it as plain JSON and reports
    a clear error if that fails, same as every other client here.
    """
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InstallError(f"{config_path} is not valid JSON: {exc}") from exc
    else:
        data = {}

    servers = data.setdefault("mcp", {})
    existing = servers.get(SERVER_NAME)
    old_command = existing.get("command") if isinstance(existing, dict) else None
    full_args = args
    if len(args) <= 1 and isinstance(old_command, list) and len(old_command) > 2:
        full_args = args + [a for a in old_command[2:] if isinstance(a, str)]

    entry = {"type": "local", "command": [command, *full_args], "enabled": True}

    if servers.get(SERVER_NAME) == entry:
        return True, entry

    servers[SERVER_NAME] = entry
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return False, entry


def merge_codex_server(config_path: Path, command: str, args: list[str]) -> tuple[bool, dict]:
    """Merge a `panel-live-server` table into the Codex CLI's config.toml.

    Codex nests each server under ``[mcp_servers.<name>]``, a TOML variant of
    the ``mcpServers`` shape ``merge_mcp_server`` handles for JSON clients.
    tomlkit is used instead of stdlib ``tomllib`` (which is read-only), so the
    rest of a hand-edited config.toml (comments, other tables, formatting)
    round-trips untouched.
    """
    if config_path.exists():
        try:
            doc = tomlkit.parse(config_path.read_text(encoding="utf-8"))
        except TOMLKitError as exc:
            raise InstallError(f"{config_path} is not valid TOML: {exc}") from exc
    else:
        doc = tomlkit.document()

    servers = doc.setdefault("mcp_servers", tomlkit.table(is_super_table=True))
    entry = tomlkit.table()
    entry["command"] = command
    entry["args"] = _merge_args(servers.get(SERVER_NAME), args)

    if servers.get(SERVER_NAME) is not None and dict(servers[SERVER_NAME]) == dict(entry):
        return True, dict(entry)

    servers[SERVER_NAME] = entry
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return False, dict(entry)


def merge_mistral_vibe_server(config_path: Path, command: str, args: list[str]) -> tuple[bool, dict]:
    """Merge a `panel-live-server` entry into Mistral Vibe's config.toml.

    Vibe lists servers as ``[[mcp_servers]]``, an array of tables identified by
    a ``name`` field rather than keyed by their own table name, so it needs its
    own merge instead of ``merge_codex_server``.
    """
    if config_path.exists():
        try:
            doc = tomlkit.parse(config_path.read_text(encoding="utf-8"))
        except TOMLKitError as exc:
            raise InstallError(f"{config_path} is not valid TOML: {exc}") from exc
    else:
        doc = tomlkit.document()

    servers = doc.setdefault("mcp_servers", tomlkit.aot())
    existing = next((s for s in servers if s.get("name") == SERVER_NAME), None)

    entry = tomlkit.table()
    entry["name"] = SERVER_NAME
    entry["transport"] = "stdio"
    entry["command"] = command
    entry["args"] = _merge_args(existing, args)

    if existing is not None and dict(existing) == dict(entry):
        return True, dict(entry)

    if existing is not None:
        servers.remove(existing)
    servers.append(entry)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return False, dict(entry)


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
