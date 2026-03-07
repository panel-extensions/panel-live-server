# panel-viz-mcp vs panel-live-server — Comparison

## Project Overview

**panel-live-server**
- Generic code execution platform + visualization server
- Dual interface: browser UI (`pls serve`) + MCP server (`pls mcp`)
- Full SQLite persistence of all snippets
- Arbitrary Python execution with "jupyter" or "panel" methods

**panel-viz-mcp**
- Curated charting service optimized for AI assistants
- 15 MCP tools covering the full visualization lifecycle
- Transient in-memory store (no persistence)
- Pre-built abstractions for 14 chart types + code generation

---

## Architecture Comparison

| Aspect | panel-live-server | panel-viz-mcp |
|--------|-------------------|---------------|
| Model | Generic code executor + visualization server | Curated charting service |
| Tool count | 2 (`show`, `list_packages`) | 15 (create_viz, dashboard, streaming, multi-chart, etc.) |
| Persistence | Full SQLite archive | Transient in-memory dict |
| Browser UI | `/feed`, `/view`, `/add`, `/admin` | None (MCP App resources only) |
| Subprocess model | One long-lived Panel server | On-demand per `launch_panel` call |
| HTML resources | 1 generic `show.html` | 4 specialized (viz, dashboard, stream, multi) |
| Security | Minimal (arbitrary exec) | AST validation + import whitelist |
| Python minimum | 3.12 | 3.10 |

---

## File Organization

**panel-live-server** (`src/panel_live_server/`)
```
app.py           Panel server entry (pages + REST endpoints)
cli.py           Typer CLI (serve/mcp/status/list)
server.py        FastMCP instance, show() tool, list_packages() tool
config.py        Pydantic Config, env var binding
manager.py       Subprocess lifecycle (start/stop/restart/health-check)
client.py        HTTP client to /api/snippet
database.py      SQLite models (Snippet), CRUD operations
endpoints.py     Tornado handlers (/api/snippet, /api/health)
utils.py         Code execution, validation, extension detection
ui.py            Shared UI components
pages/
  view_page.py   /view - renders single snippet
  feed_page.py   /feed - live-updating visualization list
  add_page.py    /add - web form to create snippets
  admin_page.py  /admin - management table
templates/
  show.html      MCP App HTML (iframe + toolbar + zoom controls)
```

**panel-viz-mcp** (`src/panel_viz_mcp/`)
```
app.py                FastMCP instance + in-memory stores (_viz_store, _panel_servers)
server.py             Entry point re-exports
chart_builders.py     Bokeh figure construction + annotations
constants.py          Chart types, URIs, limits
cdn.py                BokehJS CDN script tags
themes.py             Theme colors + CSS variables
code_generators/
  standard.py         Single-chart Panel apps (includes candlestick logic)
  geo.py              Geographic apps with GeoViews/DeckGL
  multi.py            Multi-chart grid layouts
tools/                15 MCP tools
  viz.py              create_viz, update_viz, load_data, handle_click, list_vizs
  dashboard.py        create_dashboard, apply_filter, set_theme
  stream.py           stream_data (live-updating chart)
  multi.py            create_multi_chart
  annotation.py       annotate_viz
  export.py           export_data
  panel_launch.py     launch_panel, stop_panel (subprocess management)
  custom_app.py       create_panel_app (LLM-written code execution)
resources/            4 HTML MCP App resources
  viz_html.py         Single chart viewer + toolbar
  dashboard_html.py   Dashboard with filters + stats + table
  stream_html.py      Live streaming chart
  multi_html.py       Multi-chart grid
```

---

## MCP Tools

**panel-live-server** (2 tools)
- `show(code, name, description, method, zoom)` — execute arbitrary Python, return viz URL
- `list_packages()` — list installed Python packages with versions

**panel-viz-mcp** (15 tools)
- `create_viz(kind, title, data, x, y, color)` — 14 chart types
- `update_viz(viz_id, ...)` — modify existing chart
- `load_data(file_path, kind, x, y, ...)` — load CSV/Parquet and visualize
- `handle_click(viz_id, ...)` — bidirectional click handler → AI insight
- `list_vizs()` — list active visualizations
- `create_dashboard(...)` — chart + stat tiles + data table + filter sidebar
- `apply_filter(viz_id, filters)` — server-side crossfiltering
- `set_theme(viz_id, theme)` — dark/light toggle
- `create_multi_chart(...)` — 2-4 chart grid
- `annotate_viz(viz_id, ...)` — add hline/vline/text/band/arrow
- `export_data(viz_id, format)` — export as CSV or JSON
- `launch_panel(viz_id)` — open full interactive app in browser (new window)
- `stop_panel(viz_id)` — stop subprocess
- `create_panel_app(code, title)` — launch arbitrary Panel code (with AST checks)
- `stream_data(...)` — live-updating chart

---

## HTML Template Architecture

**panel-live-server: `show.html`** (~370 lines)
- Generic iframe wrapper for any Panel URL
- Toolbar: Copy URL, Copy Code, Zoom (100%/75%/50%/25%)
- Loading spinner + 10s fallback "Open in browser" prompt if iframe is blocked
- Uses `@modelcontextprotocol/ext-apps` App SDK
- Zoom via CSS `transform: scale()`

**panel-viz-mcp: 4 specialized resources**

1. **`viz_html.py`** (~600 lines) — single chart viewer
   - Toolbar: Light/Dark toggle, Save PNG, Export CSV, **Open in Panel** button
   - Bidirectional click: chart tap → `handle_click` → insight text shown in resource
   - Auto-prompts "Open in Panel" for geo/streaming charts (banner overlay)
   - CSS theme variables for dark/light mode

2. **`dashboard_html.py`** (~900 lines)
   - Full dashboard: chart + 3-stat tiles + data table + filter sidebar
   - Filter sidebar: Select dropdowns + Range sliders
   - Crossfiltering: filter change → `apply_filter` → re-render
   - Styled Tabulator table

3. **`stream_html.py`** (~500 lines)
   - Live streaming: play/pause/reset controls
   - BokehJS client-side updates (no server round-trip per data point)

4. **`multi_html.py`** (~400 lines)
   - 2-4 chart responsive CSS Grid

---

## Code Execution & Safety

**panel-live-server**
- Direct `exec()` in `types.ModuleType` namespace
- `validate_code()`: tries to execute in isolated module, captures errors
- `find_extensions()`: infers Panel extensions from imports
- Full traceback capture stored in DB

**panel-viz-mcp**
- Tools build charts via hvPlot; no arbitrary exec for standard use
- AST validation for `create_panel_app`:
  - Allowed: panel, holoviews, hvplot, bokeh, geoviews, pandas, numpy, scipy, etc.
  - Blocked: os, sys, subprocess, http, socket, pickle, etc.
  - Blocked calls: `__import__`, `exec`, `eval`, `compile`

---

## Persistence & State

**panel-live-server**
- SQLite at `~/.panel-live-server/snippets/snippets.db`
- Full-text search virtual table (name/description/readme/app)
- Every visualization persisted; browsable via /feed and /admin

**panel-viz-mcp**
- `_viz_store` dict — transient, lost on restart
- `_panel_servers` dict tracks subprocess info
- atexit cleanup stops all running Panel subprocesses

---

## Where panel-live-server Excels

1. **Flexibility** — arbitrary Python execution works with any library
2. **Persistence** — full SQLite archive; nothing lost between sessions
3. **Browser UI** — `/add`, `/feed`, `/admin` for manual creation and management
4. **Panel-native** — direct `.servable()` support without a code generation layer
5. **Debugging** — captures tracebacks, execution time, per-snippet status in DB
6. **Extension auto-detection** — `find_extensions()` infers Panel extensions from imports
7. **Resilience** — health checks, auto-restart up to `max_restarts`, adopts healthy stale servers
8. **Production tooling** — CI, pre-commit, mypy, coverage, Diataxis docs, conda-forge packaging

---

## Where panel-viz-mcp Excels

1. **Rich, specialized HTML resources** — 4 purpose-built UIs each with dedicated toolbar/affordances
2. **"Open in Panel" button** — every resource can open the full app in a new browser window
3. **Bidirectional interactivity** — chart click → `handle_click` tool → AI insight text in resource
4. **Structured tools** — 15 domain-specific tools covering full lifecycle
5. **Code generation** — produces inspectable, gallery-quality Panel/Bokeh code
6. **Dark/light theme toggle** — CSS variable system with client-side toggle button
7. **Dashboard template** — full layout (chart + stats + table + filters) from one tool call
8. **Streaming chart** — dedicated tool + resource with play/pause/reset controls
9. **Data export** — Save PNG and Export CSV buttons built into every resource

---

## Improvement Opportunities for panel-live-server

Prioritized from highest to lowest impact:

### 1. "Open in Panel" button in `show.html` — HIGH VALUE, IMPLEMENTED
panel-viz-mcp's toolbar has an "Open in Panel" button. Implemented in panel-live-server.

**Implementation notes (hard-won):**
- `window.open()` is **blocked** by the iframe sandbox the MCP client wraps resources in — nothing happens
- panel-viz-mcp works around this by calling `app.callServerTool("launch_panel")` → Python `webbrowser.open(url)` server-side.
  This is **not appropriate** for panel-live-server: the Panel server may be deployed remotely, so opening a browser on the server process would open it on the wrong machine.
- **Working solution**: use an `<a href target="_blank">` styled as a button. Browsers treat anchor navigation differently from script-initiated popups; `<a target="_blank">` is more likely to pass iframe sandbox rules. The `href` and `target` are set dynamically when the URL arrives; `aria-disabled="true"` (no `href`) keeps it inert before that.

### 2. Rename "Copy URL" → "Share URL" — LOW EFFORT, BETTER UX
"Share URL" better communicates the intent (copying the link to share/reuse), vs the more
technical "Copy URL". Consider also placing it after "Open in Panel" if added.

### 3. Dark/light theme toggle — MEDIUM, SELF-CONTAINED
viz_html.py uses CSS variables + a client-side toggle button. Can be added to `show.html`
without touching the Panel server.

### 4. Bidirectional callback system — HIGH VALUE, COMPLEX
Chart click → MCP tool call → response displayed in resource. Requires a new tool and JS
`postMessage`/fetch wiring in `show.html`.

### 5. Additional specialized HTML resources — MEDIUM
Purpose-built resources for dashboard (stat tiles + filter sidebar), streaming (play/pause),
and multi-chart (CSS grid) use cases. These would be returned from `show()` based on detected
content type.

### 6. Code generation helpers — MEDIUM
Additional MCP tools that accept structured params (kind, x, y, data) and generate Panel code,
reducing LLM code-writing burden for common chart types.

### 7. Export buttons — LOW, EASY
"Save PNG" and "Export CSV" buttons in the toolbar (panel-viz-mcp has both in every resource).

### 8. AST validation security mode — LOW
Optional import whitelist + blocked-call checker for `validate_code()`, similar to
panel-viz-mcp's `create_panel_app` security checks.
