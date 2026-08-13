# Configure Panel Live Server

This guide shows you how to configure Panel Live Server, the Panel web server that executes
Python code snippets and renders interactive visualizations.

## Prerequisites

- Panel Live Server installed, see [Installation](../tutorials/installation.md)

---

## Default Configuration

Panel Live Server runs on `localhost` with sensible defaults. No configuration file is
required for local use.

| Setting | Default | Description |
|---|---|---|
| Port | per-environment (base `5077`) | Panel server port, derived from the active interpreter unless `PANEL_LIVE_SERVER_PORT` is set. Each Python environment gets its own port so servers in different environments do not collide. |
| Host | `localhost` | Server host address |
| Database | `~/.panel-live-server/snippets/snippets.db` | SQLite database path |
| Max restarts | `3` | Maximum automatic restarts on failure |
| Draft retention | `24` hours | How long a draft the AI has not shown you is kept before being swept. Set with `PANEL_LIVE_SERVER_DRAFT_RETENTION_HOURS`. |
| AI instructions | built-in | Prompts sent to the AI in MCP mode. Override per section with `pls mcp --prompts <file>`. |

---

## Configure via Environment Variables

All settings are controlled through environment variables:

**macOS / Linux:**

```bash
export PANEL_LIVE_SERVER_PORT=9999
export PANEL_LIVE_SERVER_HOST=127.0.0.1
export PANEL_LIVE_SERVER_DB_PATH=/data/my-snippets.db
export PANEL_LIVE_SERVER_MAX_RESTARTS=5
export PANEL_LIVE_SERVER_DRAFT_RETENTION_HOURS=24
```

**Windows (PowerShell):**

```powershell
$env:PANEL_LIVE_SERVER_PORT="9999"
$env:PANEL_LIVE_SERVER_HOST="127.0.0.1"
$env:PANEL_LIVE_SERVER_DB_PATH="C:\data\my-snippets.db"
$env:PANEL_LIVE_SERVER_MAX_RESTARTS="5"
```

**Windows (Command Prompt):**

```bat
set PANEL_LIVE_SERVER_PORT=9999
set PANEL_LIVE_SERVER_HOST=127.0.0.1
set PANEL_LIVE_SERVER_DB_PATH=C:\data\my-snippets.db
set PANEL_LIVE_SERVER_MAX_RESTARTS=5
```

Then start the server, in standalone mode:

```bash
pls serve
```

Or in MCP mode:

```bash
pls mcp
```

---

## Configure via CLI Flags

Alternatively, pass settings directly to `pls serve`:

```bash
pls serve --port 9999
pls serve --host 0.0.0.0 --port 8080
pls serve --db-path /data/my-snippets.db
```

Run `pls serve --help` for the full list of options.

---


## Custom Database Location

By default the SQLite database is stored at `~/.panel-live-server/snippets/snippets.db`.
To use a different location.

**macOS / Linux:**

```bash
export PANEL_LIVE_SERVER_DB_PATH=/path/to/your/snippets.db
```

**Windows (PowerShell):**

```powershell
$env:PANEL_LIVE_SERVER_DB_PATH="C:\path\to\your\snippets.db"
```

```bash
pls serve
```

Or via CLI:

```bash
pls serve --db-path /path/to/your/snippets.db
```


## Configuring Auto-Restart Behaviour

Panel Live Server automatically restarts the Panel subprocess if it becomes unhealthy, up to
`max_restarts` times. Adjust this limit.

**macOS / Linux:**

```bash
export PANEL_LIVE_SERVER_MAX_RESTARTS=5
```

**Windows (PowerShell):**

```powershell
$env:PANEL_LIVE_SERVER_MAX_RESTARTS="5"
```

```bash
pls mcp
```

Set to `0` to disable automatic restarts.


## Next Steps

- [Run as MCP Server](run-as-mcp-server.md): connect to your AI client
- [Customize AI Instructions](customize-ai-instructions.md): add your own rules to the AI prompts
- [Troubleshoot Issues](troubleshooting.md): common problems and solutions
- [Architecture](../explanation/architecture.md): understand how the components fit together
- [Installation Tutorial](../tutorials/installation.md): create your first visualization
- [API Reference](../reference/panel_live_server.md): full reference documentation
