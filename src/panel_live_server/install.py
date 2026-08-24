"""Register panel-live-server with an AI client's MCP configuration."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import tomlkit
from tomlkit.exceptions import TOMLKitError
from tomlkit.items import AoT, Table

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
    """Return the Windsurf editor's MCP config file path.

    The editor and the Cascade plugin (Windsurf inside another IDE) keep
    separate files, and only differ by the ``windsurf`` segment. This returns
    the editor's; plugin users pass ``--config-path ~/.codeium/mcp_config.json``.
    """
    return Path("~/.codeium/windsurf/mcp_config.json").expanduser()


def cline_config_path() -> Path:
    """Return Cline's MCP config file path.

    Cline 4 moved this out of VS Code's per-extension storage and into a home
    directory of its own, so one file now serves the VS Code extension, the CLI,
    and the JetBrains plugin alike. That also means no per-OS branching and no
    special case for VS Code forks: Insiders, Cursor, and VSCodium all read the
    same path. The old ``globalStorage/saoudrizwan.claude-dev`` location is now
    only a migration source, so writing there would have no effect.
    """
    if explicit := os.environ.get("CLINE_MCP_SETTINGS_PATH", "").strip():
        return Path(explicit).expanduser()
    if data_dir := os.environ.get("CLINE_DATA_DIR", "").strip():
        base = Path(data_dir).expanduser()
    elif cline_dir := os.environ.get("CLINE_DIR", "").strip():
        base = Path(cline_dir).expanduser() / "data"
    else:
        base = Path("~/.cline/data").expanduser()
    return base / "settings" / "cline_mcp_settings.json"


def jetbrains_config_path() -> Path:
    """Return Junie's global MCP config file path.

    Junie, not JetBrains AI Assistant: the two are separate products, and only
    Junie documents a config file. AI Assistant is set up from the IDE settings
    UI instead, so there is nothing for us to write there.
    """
    return Path("~/.junie/mcp/mcp.json").expanduser()


def gemini_cli_config_path() -> Path:
    """Return the Gemini CLI's global settings file path."""
    return Path("~/.gemini/settings.json").expanduser()


def antigravity_config_path() -> Path:
    """Return Google Antigravity's global MCP config file path.

    Antigravity's docs give this as ``~/.gemini/config/mcp_config.json``, but the
    shipping build reads ``~/.gemini/antigravity/mcp_config.json`` instead: that
    is the directory it calls its data directory, and the string
    ``.gemini/config`` appears nowhere in the application bundle. Since the two
    layouts presumably belong to different versions, an existing file wins over
    either default, so whichever one the installed build already uses is the one
    that gets updated.
    """
    installed = Path("~/.gemini/antigravity/mcp_config.json").expanduser()
    documented = Path("~/.gemini/config/mcp_config.json").expanduser()
    if not installed.exists() and documented.exists():
        return documented
    return installed


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
    """Return the Codex CLI's global config file path, honouring ``CODEX_HOME``."""
    home = os.environ.get("CODEX_HOME", "").strip()
    base = Path(home).expanduser() if home else Path("~/.codex").expanduser()
    return base / "config.toml"


def mistral_vibe_config_path() -> Path:
    """Return Mistral Vibe's global config file path, honouring ``VIBE_HOME``."""
    home = os.environ.get("VIBE_HOME", "").strip()
    base = Path(home).expanduser() if home else Path("~/.vibe").expanduser()
    return base / "config.toml"


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

    Fields on an existing entry that this does not manage are carried over rather
    than rebuilt away. Several clients write their own alongside ours (Kiro adds
    ``disabled`` and ``autoApprove``, others add ``env`` or ``timeout``), and
    re-running an install should not silently reset a server the user had tuned.

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
    existing = servers.get(SERVER_NAME)
    entry: dict = dict(existing) if isinstance(existing, dict) else {}
    if entry_type:
        entry["type"] = entry_type
    entry["command"] = command
    entry["args"] = _merge_args(existing, args)
    # setdefault, not update: a `tools` list the user narrowed by hand is theirs.
    for key, value in (extra_fields or {}).items():
        entry.setdefault(key, value)

    if existing == entry:
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

    # Same as merge_mcp_server: keep fields we do not manage, e.g. `environment`
    # or a `timeout` the user raised for a slow start-up.
    entry: dict = dict(existing) if isinstance(existing, dict) else {}
    entry["type"] = "local"
    entry["command"] = [command, *full_args]
    entry.setdefault("enabled", True)

    if existing == entry:
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
    # A config can legally hold `mcp_servers = []` or `mcp_servers = {}` instead
    # of the `[mcp_servers.<name>]` tables Codex documents. Writing a sub-table
    # into either produces TOML that no longer parses, so refuse rather than
    # hand back a config the client can no longer read.
    if not isinstance(servers, Table):
        raise InstallError(
            f"{config_path} has an `mcp_servers` entry that is not a table. Remove that line (or convert it to `[mcp_servers.<name>]` tables), then re-run this."
        )

    entry = tomlkit.table()
    entry["command"] = command
    entry["args"] = _merge_args(servers.get(SERVER_NAME), args)

    if servers.get(SERVER_NAME) is not None and dict(servers[SERVER_NAME]) == dict(entry):
        return True, dict(entry)

    servers[SERVER_NAME] = entry
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return False, dict(entry)


def _vibe_servers_aot(doc, config_path: Path) -> AoT:
    """Return Vibe's ``mcp_servers`` as an array of tables, converting if needed.

    ``mcp_servers = []`` is the same key in a different TOML shape, and a config
    can hold it either because a tool wrote it or because someone cleared the
    list by hand. Appending a table to that array writes a file that no longer
    parses, so the array form is rewritten as a real ``[[mcp_servers]]`` first.
    Entries already written inline are carried across rather than dropped.
    """
    servers = doc.get("mcp_servers")
    if isinstance(servers, AoT):
        return servers
    if servers is None:
        return doc.setdefault("mcp_servers", tomlkit.aot())

    try:
        existing = list(servers)
    except TypeError:
        existing = None
    if existing is None or any(not isinstance(s, dict) for s in existing):
        raise InstallError(
            f"{config_path} has an `mcp_servers` entry that is not a list of servers. "
            f"Remove that line (or convert it to `[[mcp_servers]]` entries), then re-run this."
        )

    converted = tomlkit.aot()
    for server in existing:
        table = tomlkit.table()
        for key, value in server.items():
            table[key] = value
        converted.append(table)
    doc["mcp_servers"] = converted
    return doc["mcp_servers"]


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

    servers = _vibe_servers_aot(doc, config_path)
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
