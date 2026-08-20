# Tutorial: Installation

In this tutorial you'll install Panel Live Server so that the `pls` command is available in your
terminal. By the end, `pls --version` will print the installed version.

## What You'll Need

- Python 3.12 or later
- A package manager: [`pixi`](https://pixi.sh), [`uv`](https://docs.astral.sh/uv/), or `pip` (built into Python)

---

## Install Panel Live Server

=== "pixi"

    Initialize a project:

    ```bash
    pixi init
    pixi add python
    ```

    Install:

    ```bash
    pixi add --pypi "panel-live-server[pydata]"
    ```

    Install the browser the `screenshot` tool needs:

    ```bash
    pixi run pls install-browser
    ```

    Find the `pls` path:

    **macOS / Linux:**

    ```bash
    pixi run which pls
    # typically: /path/to/project/.pixi/envs/default/bin/pls
    ```

    **Windows:**

    ```powershell
    pixi run where.exe pls
    # typically: .pixi\envs\default\Library\bin\pls.exe
    ```

=== "uv"

    Install:

    ```bash
    uv tool install "panel-live-server[pydata]"
    ```

    Install the browser the `screenshot` tool needs:

    ```bash
    pls install-browser
    ```

    Find the `pls` path:

    **macOS / Linux:**

    ```bash
    which pls
    # typically: /home/<user>/.local/bin/pls
    ```

    **Windows:**

    ```powershell
    where.exe pls
    # typically: %USERPROFILE%\.local\bin\pls.exe
    ```

=== "pip"

    Create and activate a virtual environment.

    **macOS / Linux:**

    ```bash
    python -m venv venv
    source venv/bin/activate
    ```

    **Windows (PowerShell):**

    ```powershell
    python -m venv venv
    venv\Scripts\Activate.ps1
    ```

    **Windows (Command Prompt):**

    ```bat
    python -m venv venv
    venv\Scripts\activate.bat
    ```

    Install:

    ```bash
    pip install "panel-live-server[pydata]"
    ```

    Install the browser the `screenshot` tool needs:

    ```bash
    pls install-browser
    ```

    Find the `pls` path:

    **macOS / Linux:**

    ```bash
    which pls
    # typically: /path/to/venv/bin/pls
    ```

    **Windows:**

    ```powershell
    where.exe pls
    # typically: .\venv\Scripts\pls.exe
    ```

The core install ships the HoloViz visualization stack — hvplot, holoviews, panel, and bokeh.
The `[pydata]` extra adds the wider PyData ecosystem on top:

> matplotlib · plotly · seaborn · altair · polars · duckdb · datashader · geoviews · plotnine · pyarrow · scikit-learn · and more

!!! tip "Only need the core server?"
    Install without extras if you only want to serve your own code and manage packages yourself:
    ```bash
    pixi add --pypi panel-live-server
    uv tool install panel-live-server
    pip install panel-live-server
    ```

---

## Verify the installation

```bash
pls --version
```

You should see the installed version printed. If the command is not found, ensure your uv tools
directory is on your PATH, run `uv tool update-shell` and restart your terminal.

---

## Connect to your MCP client

=== "VS Code"

    From the project you want it in:

    ```bash
    pls install vscode
    ```

    VS Code reads MCP servers per project, so this writes `.vscode/mcp.json` in the
    directory you run it from, filling in the absolute path to `pls` for you.

    To set it up by hand instead, add to `.vscode/mcp.json` (create if it doesn't exist):

    ```json
    {
      "servers": {
        "panel-live-server": {
          "type": "stdio",
          "command": "/path/to/pls",
          "args": ["mcp"]
        }
      }
    }
    ```

    !!! warning "Use your absolute path"
        Replace `"command": "/path/to/pls"` with the path printed by `which pls` above,
        e.g. `"command": "/home/user/.local/bin/pls"`

=== "Cursor"

    ```bash
    pls install cursor
    ```

    To set it up by hand instead, add to `~/.cursor/mcp.json`:

    ```json
    {
      "mcpServers": {
        "panel-live-server": {
          "command": "/path/to/pls",
          "args": ["mcp"]
        }
      }
    }
    ```

    !!! warning "Use your absolute path"
        Replace `"command": "/path/to/pls"` with the path printed by `which pls` above,
        e.g. `"command": "/home/user/.local/bin/pls"`

    Open Cursor Settings → MCP and verify the green dot. Use Agent mode in chat.

=== "Claude Desktop"

    ```bash
    pls install claude
    ```

    This writes the entry below into the config file for your OS, filling in the
    absolute path to the `pls` you just installed. Other servers already configured
    there are left alone, as are any flags an existing `panel-live-server` entry
    passed after `mcp` (a `--prompts` file, say), so re-running it is safe.

    To set the config up by hand instead, edit the file for your OS:

    - **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
    - **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
    - **Linux:** `~/.config/Claude/claude_desktop_config.json`

    ```json
    {
      "mcpServers": {
        "panel-live-server": {
          "command": "/path/to/pls",
          "args": ["mcp"]
        }
      }
    }
    ```

    !!! warning "Use your absolute path"
        Replace `"command": "/path/to/pls"` with the path printed by `which pls` above,
        e.g. `"command": "/home/user/.local/bin/pls"`

    Restart Claude Desktop.

    !!! note "Enable the connector in Cowork"
        To use the `show` tool from Cowork, open **Customize → Connectors →
        panel-live-server** and set its permission to **Always Allow** (runs
        without prompting) or **Needs approval** (asks before each call). If the
        connector is left disabled, the tool won't be available in Cowork.

=== "Claude Code"

    ```bash
    pls install claude-code
    ```

    Claude Code keeps its own MCP registry rather than a config file to edit, so this
    runs `claude mcp add` for you with the absolute path filled in. To run it yourself:

    ```bash
    claude mcp add panel-live-server -- /path/to/pls mcp
    ```

    !!! warning "Use your absolute path"
        Replace `/path/to/pls` with the path printed by `which pls` above,
        e.g. `claude mcp add panel-live-server -- /home/user/.local/bin/pls mcp`

    If the server is already registered, remove it first with
    `claude mcp remove panel-live-server`.

=== "Windsurf"

    ```bash
    pls install windsurf
    ```

    To set it up by hand instead, add to `~/.codeium/windsurf/mcp_config.json`:

    ```json
    {
      "mcpServers": {
        "panel-live-server": {
          "command": "/path/to/pls",
          "args": ["mcp"]
        }
      }
    }
    ```

    !!! warning "Use your absolute path"
        Replace `"command": "/path/to/pls"` with the path printed by `which pls` above,
        e.g. `"command": "/home/user/.local/bin/pls"`

    Open the MCP servers panel (hammer icon) in Cascade and confirm it's running.

=== "Cline"

    ```bash
    pls install cline
    ```

    Cline stores this separately from `.vscode/mcp.json`, in VS Code's own
    per-extension storage. To set it up by hand instead, add to
    `cline_mcp_settings.json` (open it from Cline's "Edit MCP Settings" button):

    ```json
    {
      "mcpServers": {
        "panel-live-server": {
          "command": "/path/to/pls",
          "args": ["mcp"]
        }
      }
    }
    ```

    !!! warning "Use your absolute path"
        Replace `"command": "/path/to/pls"` with the path printed by `which pls` above,
        e.g. `"command": "/home/user/.local/bin/pls"`

    Open Cline's MCP Servers panel and confirm panel-live-server is connected.

=== "JetBrains / Junie"

    ```bash
    pls install jetbrains
    ```

    To set it up by hand instead, add to `~/.junie/mcp/mcp.json`:

    ```json
    {
      "mcpServers": {
        "panel-live-server": {
          "command": "/path/to/pls",
          "args": ["mcp"]
        }
      }
    }
    ```

    !!! warning "Use your absolute path"
        Replace `"command": "/path/to/pls"` with the path printed by `which pls` above,
        e.g. `"command": "/home/user/.local/bin/pls"`

    Restart the IDE, or reopen Junie's MCP settings, for the change to take effect.

=== "Gemini CLI"

    ```bash
    pls install gemini-cli
    ```

    This writes into `~/.gemini/settings.json`, leaving every other setting there
    (theme, auth, other servers) untouched. To set it up by hand instead, add:

    ```json
    {
      "mcpServers": {
        "panel-live-server": {
          "command": "/path/to/pls",
          "args": ["mcp"]
        }
      }
    }
    ```

    !!! warning "Use your absolute path"
        Replace `"command": "/path/to/pls"` with the path printed by `which pls` above,
        e.g. `"command": "/home/user/.local/bin/pls"`

    Restart the Gemini CLI for the change to take effect.

=== "Antigravity"

    ```bash
    pls install antigravity
    ```

    To set it up by hand instead, add to `~/.gemini/config/mcp_config.json`:

    ```json
    {
      "mcpServers": {
        "panel-live-server": {
          "command": "/path/to/pls",
          "args": ["mcp"]
        }
      }
    }
    ```

    !!! warning "Use your absolute path"
        Replace `"command": "/path/to/pls"` with the path printed by `which pls` above,
        e.g. `"command": "/home/user/.local/bin/pls"`

    Open Manage MCP Servers in Antigravity and confirm it's connected.

=== "Kiro"

    ```bash
    pls install kiro
    ```

    To set it up by hand instead, add to `~/.kiro/settings/mcp.json`:

    ```json
    {
      "mcpServers": {
        "panel-live-server": {
          "command": "/path/to/pls",
          "args": ["mcp"]
        }
      }
    }
    ```

    !!! warning "Use your absolute path"
        Replace `"command": "/path/to/pls"` with the path printed by `which pls` above,
        e.g. `"command": "/home/user/.local/bin/pls"`

    Reload Kiro, or open the MCP panel, for the change to take effect.

=== "Copilot CLI"

    ```bash
    pls install copilot
    ```

    This is the standalone `copilot` CLI, not VS Code's Copilot Chat (which already
    reads `.vscode/mcp.json`, see the VS Code tab). To set it up by hand instead, add
    to `~/.copilot/mcp-config.json`:

    ```json
    {
      "mcpServers": {
        "panel-live-server": {
          "type": "local",
          "command": "/path/to/pls",
          "args": ["mcp"],
          "tools": ["*"]
        }
      }
    }
    ```

    !!! warning "Use your absolute path"
        Replace `"command": "/path/to/pls"` with the path printed by `which pls` above,
        e.g. `"command": "/home/user/.local/bin/pls"`

    Run `/mcp show` in the Copilot CLI to confirm panel-live-server is connected.

=== "Kilo Code"

    ```bash
    pls install kilo-code
    ```

    Kilo Code packs the command and its arguments into one array under a `mcp` key,
    rather than separate `command`/`args` fields under `mcpServers`. To set it up by
    hand instead, add to `~/.config/kilo/kilo.jsonc`:

    ```json
    {
      "mcp": {
        "panel-live-server": {
          "type": "local",
          "command": ["/path/to/pls", "mcp"],
          "enabled": true
        }
      }
    }
    ```

    !!! warning "Use your absolute path"
        Replace `/path/to/pls` with the path printed by `which pls` above,
        e.g. `"command": ["/home/user/.local/bin/pls", "mcp"]`

    Reload the window for the change to take effect.

=== "Codex CLI"

    ```bash
    pls install codex
    ```

    Codex uses TOML, not JSON. To set it up by hand instead, add to `~/.codex/config.toml`:

    ```toml
    [mcp_servers.panel-live-server]
    command = "/path/to/pls"
    args = ["mcp"]
    ```

    !!! warning "Use your absolute path"
        Replace `/path/to/pls` with the path printed by `which pls` above,
        e.g. `command = "/home/user/.local/bin/pls"`

    Restart the Codex CLI for the change to take effect.

=== "Mistral Vibe"

    ```bash
    pls install mistral-vibe
    ```

    Vibe also uses TOML, and lists servers as an array of tables identified by a
    `name` field rather than keyed by their own table name. To set it up by hand
    instead, add to `~/.vibe/config.toml`:

    ```toml
    [[mcp_servers]]
    name = "panel-live-server"
    transport = "stdio"
    command = "/path/to/pls"
    args = ["mcp"]
    ```

    !!! warning "Use your absolute path"
        Replace `/path/to/pls` with the path printed by `which pls` above,
        e.g. `command = "/home/user/.local/bin/pls"`

    Restart Vibe for the change to take effect.

=== "claude.ai"

    claude.ai requires HTTP transport and a public URL. You can use any tunneling service
    (ngrok, Cloudflare, localhost.run, etc.); this example uses Cloudflare.

    **Terminal 1**: start the MCP server:

    ```bash
    /path/to/pls mcp --transport http --port 8001
    ```

    !!! warning "Use your absolute path"
        Replace `/path/to/pls` with the path printed by `which pls` above,
        e.g. `/home/user/.local/bin/pls mcp --transport http --port 8001`

    **Terminal 2**: tunnel for the MCP server:

    ```bash
    cloudflared tunnel --url http://localhost:8001
    ```

    **Terminal 3**: tunnel for the Panel server:

    ```bash
    cloudflared tunnel --url http://localhost:5077
    ```

    Stop Terminal 1, then set the Panel tunnel URL.

    **macOS / Linux:**

    ```bash
    export PANEL_LIVE_SERVER_EXTERNAL_URL=<url-from-terminal-3>
    ```

    **Windows (PowerShell):**

    ```powershell
    $env:PANEL_LIVE_SERVER_EXTERNAL_URL="<url-from-terminal-3>"
    ```

    And restart:

    ```bash
    /path/to/pls mcp --transport http --port 8001
    ```

    Then go to claude.ai → Settings → Connectors → Add custom connector and enter
    `<url-from-terminal-2>/mcp` as the URL.

Once connected, ask your AI: *"Show me a scatter plot of this data using the show tool."*

---

**Without an AI assistant**: use the REST API or the browser UI directly.

=== "REST API"

    ```python
    import requests

    r = requests.post(
        "http://localhost:5077/api/snippet",
        json={
            "code": "import panel as pn\npn.widgets.IntSlider(name='x', start=0, end=100)",
            "name": "Slider",
            "method": "inline",
        }
    )
    print(r.json()["url"])  # http://localhost:5077/view?id=...
    ```

=== "Standalone"

    ```bash
    /path/to/pls serve
    # Open http://localhost:5077/add in your browser
    ```

---

## Add packages to the server environment

Panel Live Server executes your Python snippets using the packages installed *in the environment
you installed it into*. It inherits that environment rather than defining its own, so you are free
to add or upgrade anything you need there. To add a package:

=== "pixi"

    ```bash
    pixi add --pypi my-package
    ```

    For example, to add `prophet`:

    ```bash
    pixi add --pypi prophet
    ```

    !!! note "Upgrading"
        To upgrade to the latest version:
        ```bash
        pixi upgrade panel-live-server
        ```

=== "uv"

    ```bash
    uv tool install --with my-package "panel-live-server[pydata]"
    ```

    You can chain multiple `--with` flags:

    ```bash
    uv tool install --with prophet --with xgboost "panel-live-server[pydata]"
    ```

    !!! note "Upgrading"
        To upgrade to the latest version:
        ```bash
        uv tool upgrade panel-live-server
        ```

=== "pip"

    Activate the environment you installed `pls` into, then install as usual:

    ```bash
    pip install my-package
    ```

    For example, to add `prophet`:

    ```bash
    pip install prophet
    ```

    !!! note "Upgrading"
        To upgrade to the latest version:
        ```bash
        pip install --upgrade panel-live-server
        ```

=== "conda"

    Activate the environment you installed `pls` into, then install as usual:

    ```bash
    conda activate my-env
    conda install my-package
    ```

    For example, to add `prophet`:

    ```bash
    conda activate my-env
    conda install prophet
    ```

    !!! note "Upgrading"
        To upgrade to the latest version:
        ```bash
        conda update panel-live-server
        ```

No server restart is needed, the package is available immediately the next time the server starts.

---

## What You've Learned

- Install Panel Live Server as a uv tool with the `[pydata]` extras
- Verify the installation with `pls --version`
- Add extra packages to the server environment with `--with`

## Next Steps

- **[Use the standalone server](standalone-server.md)**: create, view, and manage visualizations
- **[Use the MCP server](mcp-server.md)**: enable AI assistants to render visualizations in your IDE
