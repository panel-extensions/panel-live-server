# Contributing

Contributions are welcome! This guide walks you through forking the repository, setting up a
local development environment, and connecting your local build to an MCP client.

---

## Step 1: Fork and clone

1. [Fork the repository](https://github.com/panel-extensions/panel-live-server/fork) on GitHub.
2. Clone your fork:

```bash
git clone https://github.com/<your-username>/panel-live-server.git
cd panel-live-server
```

3. Create a feature branch:

```bash
git checkout -b feature/YourFeature
```

---

## Step 2: Install

=== "pixi"

    Install the environment:

    ```bash
    pixi install
    ```

    Run the post-install step (editable install plus Chromium setup):

    ```bash
    pixi run postinstall
    ```

    Find the `pls` path:

    **macOS / Linux:**

    ```bash
    pixi run which pls
    # typically: /path/to/panel-live-server/.pixi/envs/default/bin/pls
    ```

    **Windows:**

    ```powershell
    pixi run where.exe pls
    # typically: .pixi\envs\default\Library\bin\pls.exe
    ```

    !!! note
        Prefix commands with `pixi run` (e.g. `pixi run pytest`) to use the pixi env without
        activating it. `pixi run postinstall` already installs Chromium automatically.

=== "uv"

    Create and activate a virtual environment.

    **macOS / Linux:**

    ```bash
    uv venv
    source .venv/bin/activate
    ```

    **Windows (PowerShell):**

    ```powershell
    uv venv
    .venv\Scripts\Activate.ps1
    ```

    **Windows (Command Prompt):**

    ```bat
    uv venv
    .venv\Scripts\activate.bat
    ```

    Install the package in editable mode:

    ```bash
    uv pip install -e ".[dev]"
    ```

    Install the browser binary needed by the `screenshot` MCP tool:

    ```bash
    playwright install chromium
    ```

    Find the `pls` path:

    **macOS / Linux:**

    ```bash
    which pls
    # typically: /path/to/panel-live-server/.venv/bin/pls
    ```

    **Windows:**

    ```powershell
    where.exe pls
    # typically: .venv\Scripts\pls.exe
    ```

    !!! note
        Re-activate the venv in every new terminal (see above).
        `playwright install chromium` is a one-time step that downloads the browser binary
        (~150 MB) required by the `screenshot` MCP tool.

---

## Step 3: Install pre-commit hooks

=== "pixi"

    ```bash
    pixi run lint-install
    ```

=== "uv"

    ```bash
    pre-commit install
    ```

---

## Step 4: Connect to your MCP client

### Testing your local checkout vs the released PyPI package

`pls install <client>` registers whichever `pls` ran the command, so how you invoke it
decides which build your client ends up using.

**To test your local changes** (the normal case while contributing), run the command
through pixi so it always resolves to the editable copy of the checkout you are editing,
not any other `pls` that might also be on your machine:

```bash
pixi run pls install claude    # or: cursor / vscode / claude-code
```

Check it picked the right one:

```bash
pixi run pls --version
# 0.1.0a5.post1.dev52+gda3756c94 -- has a dev/git-hash suffix: editable, tracks your checkout
```

**To test the released PyPI package instead** (comparing behaviour, or confirming a bug
only shows up in a release, not your branch), install it separately and run its own
`pls install`:

```bash
pip install panel-live-server    # or: uv tool install panel-live-server
pls install claude
```

```bash
pls --version
# 0.1.0a5 -- plain version, no dev/git-hash suffix: this is the frozen PyPI build
```

!!! warning "Registering one replaces the other"
    Both point the *same* client at whichever `pls` you last ran `install` with, so
    running the PyPI one after the pixi one switches Claude Desktop (or Cursor, etc.)
    over to the released build, and vice versa. To register a specific build without
    relying on which `pls` happens to run the command, pass it explicitly:
    `pls install claude --command /path/to/pls`.

Once you've picked the build to register, add it to your client:

=== "VS Code"

    ```bash
    pixi run pls install vscode
    ```

    Run it from the project root: VS Code reads `.vscode/mcp.json` per project, so this
    writes it relative to your current directory.

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
        Replace `"command": "/path/to/pls"` with the path printed above,
        e.g. `"command": "/path/to/panel-live-server/.venv/bin/pls"`

=== "Cursor"

    ```bash
    pixi run pls install cursor
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
        Replace `"command": "/path/to/pls"` with the path printed above,
        e.g. `"command": "/path/to/panel-live-server/.venv/bin/pls"`

    Open Cursor Settings → MCP and verify the green dot. Use Agent mode in chat.

=== "Claude Desktop"

    ```bash
    pixi run pls install claude
    ```

    To set it up by hand instead, edit the config file for your OS:

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
        Replace `"command": "/path/to/pls"` with the path printed above,
        e.g. `"command": "/path/to/panel-live-server/.venv/bin/pls"`

    Restart Claude Desktop.

=== "Claude Code"

    ```bash
    pixi run pls install claude-code
    ```

    Or run the underlying command yourself:

    ```bash
    claude mcp add panel-live-server -- /path/to/pls mcp
    ```

    !!! warning "Use your absolute path"
        Replace `/path/to/pls` with the path printed above,
        e.g. `claude mcp add panel-live-server -- /path/to/panel-live-server/.venv/bin/pls mcp`

    If the server is already registered, remove it first with
    `claude mcp remove panel-live-server`.

=== "claude.ai"

    claude.ai requires HTTP transport and a public URL. You can use any tunneling service
    (ngrok, Cloudflare, localhost.run, etc.); this example uses Cloudflare.

    **Terminal 1**: start the MCP server:

    ```bash
    /path/to/pls mcp --transport http --port 8001
    ```

    !!! warning "Use your absolute path"
        Replace `/path/to/pls` with the path printed above,
        e.g. `/path/to/panel-live-server/.venv/bin/pls mcp --transport http --port 8001`

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

---

## Step 5: Make changes and run tests

=== "pixi"

    ```bash
    pixi run test                        # run all tests
    pixi run test-coverage               # tests + coverage report
    pixi run lint                        # lint (pre-commit on all files)
    ```

=== "uv"

    ```bash
    pytest tests/                        # run all tests
    pytest tests/test_validation.py      # run a single file
    pre-commit run --all-files           # lint
    ```

---

## Step 6: Submit a pull request

1. Commit your changes:

```bash
git commit -m 'Add some feature'
```

2. Push to your fork:

```bash
git push origin feature/YourFeature
```

3. Open a pull request against the `main` branch on GitHub.

Please ensure your code passes all tests and linting before submitting.
