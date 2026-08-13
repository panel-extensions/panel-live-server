# Troubleshoot Panel Live Server

## Port Already in Use

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

## Server Not Responding

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

## Visualizations Not Displaying

1. Confirm the server is running: `pls status`
2. Check that `show` is listed when you ask your AI assistant for available MCP tools
3. Restart the MCP server if the Panel subprocess failed to start (check startup logs)

---

## Next Steps

- [Run as MCP Server](run-as-mcp-server.md): connect to your AI client
- [Architecture](../explanation/architecture.md): understand how the components fit together
