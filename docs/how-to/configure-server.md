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

## Customising the AI Instructions

When running as an MCP server, `pls` sends the AI a set of instructions telling it how to
use the tools: prefer HoloViz packages, present the returned URL as a Markdown link, and so
on. If those defaults do not match how your team works, you can customise individual
sections without forking the project.

These sections can be customised:

| Section | What it covers |
| --- | --- |
| `library_selection` | Which plotting libraries the AI should reach for |
| `output` | How the returned URL is presented back to you |
| `draft_review` | What the AI checks when reviewing its own screenshot before showing you |
| `shown_image` | How the AI reads a screenshot when answering questions about it |

These are the parts that are genuinely a matter of preference. The rest of the prompt
describes how the server actually behaves (that validation runs automatically, that
`method='server'` is needed for interactive apps, what `SecurityError` means) and is
therefore fixed. Rewriting those would let you tell the model something untrue about
the tool, which degrades its output with no visible error.

### Adding your own rules

**Step 1 — create the file.** Put it anywhere you like, and include only the sections you
want to change. Here it is at `/home/you/my-prompts.json`:

```json
{
  "library_selection": "For sine and cosine waves use hvplot, coloured pink."
}
```

**Step 2 — point `pls mcp` at it** by adding `--prompts` and the path to your existing MCP
configuration:

=== "VS Code"

    In `.vscode/mcp.json`, add to `args`:

    ```json
    {
      "servers": {
        "panel-live-server": {
          "type": "stdio",
          "command": "/path/to/pls",
          "args": ["mcp", "--prompts", "/home/you/my-prompts.json"]
        }
      }
    }
    ```

=== "Cursor"

    In `~/.cursor/mcp.json`, add to `args`:

    ```json
    {
      "mcpServers": {
        "panel-live-server": {
          "command": "/path/to/pls",
          "args": ["mcp", "--prompts", "/home/you/my-prompts.json"]
        }
      }
    }
    ```

=== "Claude Desktop"

    In the config file for your OS:

    - **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
    - **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
    - **Linux:** `~/.config/Claude/claude_desktop_config.json`

    ```json
    {
      "mcpServers": {
        "panel-live-server": {
          "command": "/path/to/pls",
          "args": ["mcp", "--prompts", "/home/you/my-prompts.json"]
        }
      }
    }
    ```

=== "Claude Code"

    ```bash
    claude mcp add panel-live-server -- /path/to/pls mcp --prompts /home/you/my-prompts.json
    ```

    If the server is already registered, remove it first with
    `claude mcp remove panel-live-server`.

=== "claude.ai"

    Add the flag to the command you start the HTTP server with:

    ```bash
    /path/to/pls mcp --transport http --port 8001 --prompts /home/you/my-prompts.json
    ```

**Step 3 — restart the MCP server** in your client. The instructions are read once at
startup, so nothing changes until it restarts.

Your text is **added in front of** the built-in text for that section, under a header
marking it as authoritative. Your rules are read first and win where they conflict, while
the defaults stay in place below them as the fallback, so the AI still knows things like
which libraries may not be installed in this environment.

Include only what you want to change. Leave out `output` and it keeps its built-in text,
and keeps tracking upstream as you upgrade. That is the reason to customise a section
rather than copy the whole prompt: the parts you did not touch keep improving.

Writing `{"add": "..."}` instead of a bare string means exactly the same thing, if you
prefer being explicit.

### Replacing a section outright

If you want the built-in text gone rather than added to, say so explicitly:

```json
{
  "library_selection": {"replace": "Use plotly.express and nothing else."}
}
```

Use this sparingly. The default `library_selection` also tells the AI that Matplotlib,
Plotly, seaborn and Altair may not be installed, so replacing it means the AI can no longer
warn you before reaching for a package that is missing. Adding is usually what you want;
replacing is for when the default actively contradicts your policy.

### Things worth knowing

**Restart after editing.** The instructions are read once when the MCP server starts, so
changes to your prompts file only take effect after your AI client restarts the server.

**Use an absolute path**, or one starting with `~`. A relative path is resolved against the
server's working directory, which your MCP client chooses and you do not control, so it may
silently fail to load.

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
