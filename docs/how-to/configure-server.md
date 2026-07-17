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

---

## Configure via Environment Variables

All settings are controlled through environment variables:

**macOS / Linux:**

```bash
export PANEL_LIVE_SERVER_PORT=9999
export PANEL_LIVE_SERVER_HOST=127.0.0.1
export PANEL_LIVE_SERVER_DB_PATH=/data/my-snippets.db
export PANEL_LIVE_SERVER_MAX_RESTARTS=5
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

## Running as an MCP Server

To use Panel Live Server with AI assistants, start it in MCP mode:

```bash
# stdio transport (default, for Claude Desktop, Claude Code, etc.)
pls mcp

# HTTP transport
pls mcp --transport http --host 127.0.0.1 --port 8001

# SSE transport
pls mcp --transport sse
```

The Panel server starts automatically in the background. You do not need to run `pls serve`
separately.

For per-client setup instructions (VS Code, Cursor, Claude Desktop, Claude Code, claude.ai) see
[Installation → Connect to your MCP client](../tutorials/installation.md#connect-to-your-mcp-client).

### Example: Custom port via environment variable

```json
{
  "mcpServers": {
    "panel-live-server": {
      "command": "/path/to/pls",
      "args": ["mcp"],
      "env": {
        "PANEL_LIVE_SERVER_PORT": "9999"
      }
    }
  }
}
```

### Environment variables your snippets need

Some libraries a snippet imports need credentials or settings at runtime. For example, Snowflake (`SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PASSWORD`), a cloud SDK (`AWS_ACCESS_KEY_ID`), or any package that reads an API key or database URL from the environment. Snippets execute in the same process environment as the server, so set those in the same `"env"` block:

```json
{
  "mcpServers": {
    "panel-live-server": {
      "command": "/path/to/pls",
      "args": ["mcp"],
      "env": {
        "SNOWFLAKE_ACCOUNT": "your_account",
        "SNOWFLAKE_USER": "your_user",
        "SNOWFLAKE_PASSWORD": "your_password"
      }
    }
  }
}
```

Variables you `export` in a terminal do **not** reach the server when your MCP client launches it: the client starts `pls mcp` with its own environment, so runtime credentials must go in the config's `"env"` block. Do not hardcode secrets in the snippet itself. The security check rejects literal passwords, and reading them from the environment keeps them out of your code.

---

## External URL and Remote Environments

Panel Live Server needs to know its public URL when running behind a proxy or tunnel so that
visualization URLs returned to clients are reachable from the browser.

The following environment variables are detected automatically (in priority order):

| Variable(s) | Environment |
|---|---|
| `PANEL_LIVE_SERVER_EXTERNAL_URL` | Any, explicit port-inclusive override |
| `JUPYTERHUB_HOST` + `JUPYTERHUB_SERVICE_PREFIX` | JupyterHub with [jupyter-server-proxy](https://jupyter-server-proxy.readthedocs.io/) |
| `CODESPACE_NAME` + `GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN` | GitHub Codespaces |

### claude.ai

claude.ai requires a public URL. Expose the Panel server via a tunnel (Cloudflare, ngrok,
localhost.run, etc.) and set the URL before starting the MCP server:

```bash
cloudflared tunnel --url http://localhost:5077
```

Copy the printed URL and set it.

**macOS / Linux:**

```bash
export PANEL_LIVE_SERVER_EXTERNAL_URL=<url-from-above>
```

**Windows (PowerShell):**

```powershell
$env:PANEL_LIVE_SERVER_EXTERNAL_URL="<url-from-above>"
```

Then start the MCP server:

```bash
pls mcp --transport http --port 8001
```

See the [MCP server tutorial](../tutorials/mcp-server.md) for the full three-terminal setup.

### JupyterHub

`JUPYTERHUB_SERVICE_PREFIX` is set automatically by JupyterHub. However, `JUPYTERHUB_HOST`
is only set automatically in subdomain-based routing mode. In the more common path-based
routing mode, set it manually in your MCP configuration:

```json
{
  "mcpServers": {
    "panel-live-server": {
      "command": "pls",
      "args": ["mcp"],
      "env": {
        "JUPYTERHUB_HOST": "https://your-hub.example.com"
      }
    }
  }
}
```

Or set the full URL explicitly.

**macOS / Linux:**

```bash
export PANEL_LIVE_SERVER_EXTERNAL_URL="https://your-hub/user/you/proxy/5077"
```

**Windows (PowerShell):**

```powershell
$env:PANEL_LIVE_SERVER_EXTERNAL_URL="https://your-hub/user/you/proxy/5077"
```

```bash
pls mcp
```

### GitHub Codespaces

URL detection is automatic, no configuration needed.

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

---

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

---

## Troubleshooting

### Port Already in Use

Change the port:

```bash
pls serve --port 5078
```

Or find and stop the process using the port.

**macOS / Linux:**

```bash
lsof -ti:5077 | xargs kill -9
```

**Windows (PowerShell):**

```powershell
Get-Process -Id (Get-NetTCPConnection -LocalPort 5077).OwningProcess | Stop-Process -Force
```

**Windows (Command Prompt):**

```bat
netstat -ano | findstr :5077
taskkill /PID <pid-from-above> /F
```

### Server Not Responding

Check server health:

```bash
pls status
```

Or query the health endpoint directly:

```bash
curl http://localhost:5077/api/health
```

On Windows (PowerShell), `curl` is aliased to `Invoke-WebRequest`, which needs `-UseBasicParsing`
on older versions:

```powershell
Invoke-WebRequest http://localhost:5077/api/health -UseBasicParsing
```

A healthy server returns `{"status": "ok", ...}`.

### Visualizations Not Displaying

1. Confirm the server is running: `pls status`
2. Check that `show` is listed when you ask your AI assistant for available MCP tools
3. Restart the MCP server if the Panel subprocess failed to start (check startup logs)

---

## Next Steps

- [Architecture](../explanation/architecture.md): understand how the components fit together
- [Installation Tutorial](../tutorials/installation.md): create your first visualization
- [API Reference](../reference/panel_live_server.md): full reference documentation
