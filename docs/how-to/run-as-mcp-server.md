# Run Panel Live Server as an MCP Server

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

## Custom port via environment variable

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

## Next Steps

- [Customize AI Instructions](customize-ai-instructions.md): add your own rules to the AI prompts
- [Troubleshoot Issues](troubleshooting.md): common problems and solutions
