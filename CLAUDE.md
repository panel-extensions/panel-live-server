# CLAUDE.md — panel-live-server

## Project Overview

**Panel Live Server** is a local Panel web server that executes Python code snippets and renders
the resulting visualizations as live, interactive web pages — enabling humans and AI assistants
to display and inspect Python outputs in real time.

It ships two interfaces:

- **`pls serve`** — standalone web server with browser UI (/feed, /view, /add, /admin)
- **`pls mcp`** — MCP server that AI assistants (Claude, Copilot, etc.) talk to via the `show` tool

## Dev Setup

```bash
pixi install
pixi run postinstall   # editable install
```

Or with uv:

```bash
uv venv && source .venv/bin/activate
uv pip install -e .[dev]
```

## Key Commands

```bash
pixi run test                  # run tests
pixi run test-coverage         # tests + coverage report
pixi run lint                  # pre-commit on all files
pixi run lint-install          # install pre-commit hooks
pixi run docs                  # serve docs locally
pixi run docs-build            # build docs
pixi run build-wheel           # build distribution wheel
```

CLI (after install):

```bash
pls serve                      # start Panel server (default port 5077)
pls serve --port 9999          # custom port
pls mcp                        # start MCP server (stdio transport)
pls mcp --transport http       # HTTP transport
pls status                     # check if server is running
pls list packages              # list installed packages
pls list packages panel        # filter by name
```

## Architecture

```
pls mcp  (MCP server — FastMCP, stdio/http/sse)
  └── PanelServerManager
        └── pls serve  (Panel subprocess on port 5077)
              ├── POST /api/snippet   — create visualization
              ├── GET  /api/health    — health check
              └── Pages: /view  /feed  /add  /admin
```

## Source Layout

```
src/panel_live_server/
  app.py          Panel server entry point (registers pages + REST endpoints)
  cli.py          Typer CLI — pls serve / pls mcp / pls status / pls list
  server.py       FastMCP server — show() and list_packages() tools
  config.py       Config dataclass, env var binding
  manager.py      Subprocess lifecycle (start/stop/restart/health-check)
  client.py       HTTP client for /api/snippet and /api/health
  database.py     SQLite models and CRUD via pydantic + sqlite3
  endpoints.py    Tornado handlers for /api/snippet and /api/health
  utils.py        Code validation, package/extension detection
  ui.py           Shared UI components
  pages/          Panel servable pages
    feed_page.py  /feed — live-updating visualization list
    view_page.py  /view — executes and renders a single snippet
    add_page.py   /add  — web form to create snippets
    admin_page.py /admin — management table with delete/search
  templates/
    show.html     MCP App HTML (iframe + toolbar + zoom controls)
```

## Configuration (Environment Variables)

| Variable | Default | Description |
|---|---|---|
| `PANEL_LIVE_SERVER_PORT` | `5077` | Panel server port |
| `PANEL_LIVE_SERVER_HOST` | `localhost` | Panel server host |
| `PANEL_LIVE_SERVER_DB_PATH` | `~/.panel-live-server/snippets/snippets.db` | SQLite database path |
| `PANEL_LIVE_SERVER_MAX_RESTARTS` | `3` | Max automatic restarts on failure |
| `PANEL_LIVE_SERVER_EXTERNAL_URL` | — | Explicit external URL override (port-inclusive). Auto-detected from `JUPYTERHUB_HOST`+`JUPYTERHUB_SERVICE_PREFIX` (JupyterHub) or `CODESPACE_NAME`/`GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN` (Codespaces) if not set. |

## MCP Tools

- **`show(code, name, description, method, zoom)`** — execute Python, return visualization URL
  - `method`: `"jupyter"` (last expression displayed) or `"panel"` (explicit `.servable()`)
  - `zoom`: `25 | 50 | 75 | 100`
- **`list_packages()`** — list installed Python packages with versions

## Documentation Structure (Diataxis)

```
docs/
  tutorials/
    getting-started.md        Step-by-step first use (standalone + MCP)
  how-to/
    configure-server.md       Configure ports, host, DB path, restart limits
  explanation/
    architecture.md           How the server, MCP, and browser fit together
  reference/
    panel_live_server.md      Auto-generated API reference
  index.md                    Home page (includes README)
  examples.md                 Quick code examples
```

## Technology Stack

- **Panel** >=1.5.0 — web framework for data apps
- **FastMCP** >=3.0 — MCP server
- **Typer** — CLI
- **Pydantic** >=2.0 — config and data validation
- **SQLite** — persistence (via stdlib sqlite3)
- **psutil** — cross-platform process management
- **requests** — HTTP client
- **zensical** — documentation site builder
- **mkdocstrings** — API reference from docstrings

## Testing

Tests live in `tests/`. Run with `pixi run test`. UI tests (Playwright) are in `tests/ui/`
and require the `test-ui` feature: `pixi run -e test-ui test-ui`.

## Notes

- Default port **5077** (avoids the common 5000–5020 range)
- MCP server starts the Panel subprocess **eagerly** at startup (not lazily on first `show` call)
- Subprocess managed with atexit cleanup; auto-restarts up to `max_restarts` times
- Code execution uses a `types.ModuleType` namespace so Panel decorators (`@pn.cache`, `@pn.depends`) work correctly
- URLs are externalized automatically for Jupyter Server Proxy and GitHub Codespaces
