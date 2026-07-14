# Understanding Panel Live Server

Panel Live Server enables AI assistants and developers to create and manage Python visualizations
through a dedicated local web server. This document explains the architecture, design decisions,
and key concepts.

## Architecture Overview

Panel Live Server uses a two-process architecture:

```
MCP Client (Claude, Copilot, etc.)
  │  MCP protocol (stdio / HTTP / SSE)
  ▼
pls mcp, MCP Server (FastMCP)
  │  HTTP  POST /api/snippet
  │  HTTP  GET  /api/health
  ▼
pls serve, Panel Server (subprocess, port 5077)
  │  SQLite  ~/.panel-live-server/snippets/snippets.db
  ▼
Browser, /view  /feed  /add  /admin
```

**MCP Server** (`pls mcp`): Hosts the `show` and `screenshot` MCP tools. Starts the
Panel server as a subprocess and manages its lifecycle.

**Panel Server** (`pls serve`): Executes Python code and serves visualizations as web pages.
Exposes a REST API and four browser-accessible pages.

**Browser**: Displays visualizations and management interfaces.

---

## MCP Tools

The MCP server exposes two tools to the AI assistant, meant to be used together in a
typical session.

The environment is fixed — packages cannot be installed on the fly — so the MCP server's
instructions steer the assistant to write visualizations with **HoloViz packages** (hvPlot,
HoloViews, Panel) first, falling back only when HoloViz cannot do the job. The core install
also ships **Bokeh** (HoloViz's default backend) and the **ECharts** / **deck.gl** Panel panes,
which are always available with no extra package. (Other well-known libraries such as Matplotlib,
Plotly, seaborn, or Altair live in the optional `[pydata]` extra and may be absent.) If an import is missing, `show`
reports it and the assistant rewrites the code rather than assuming availability. (A human can
still inspect the environment directly with the `pls list packages` CLI command.)

### `show`: validate, then render the visualization

The primary tool for turning code into a live visualization. Validation is built in: before
anything is stored or rendered, `show` runs a chain of static checks and, if any fail, returns
a quiet "Refining…" retry payload instead of a broken render — no separate `validate` call is
needed.

The checks, in order:

1. **Syntax**: `ast.parse()` catches syntax errors early
2. **Security**: ruff security rules plus a blocked-import list
3. **Package availability**: every import must already be installed in the server environment
4. **Panel extensions**: required extensions declared via `pn.extension()` (`server` method
   only, the `inline` method injects them automatically)

When the checks pass, `show`:

1. POSTs the snippet to the Panel server's `/api/snippet` endpoint
2. The Panel server stores the snippet in SQLite and returns a URL
3. The MCP server returns the URL to the AI assistant
4. The user accesses the visualization via URL in their browser (or inline in the MCP App UI)

```python
show(
    code="df.hvplot.bar(x='Product', y='Sales')",
    name="Sales Chart",
    description="Bar chart of product sales",
    method="inline",
    zoom=75
)
```

The tool accepts:

- **code** (required): Python code to execute
- **name**: Human-readable title
- **description**: One-sentence explanation
- **method**: Execution method, `"inline"` (default) or `"server"`
- **zoom**: Initial zoom level, `25`, `50`, `75`, or `100`

### `screenshot`: see the result, don't guess

`show` returns a live URL, but an AI assistant cannot open a browser to look at it. The
`screenshot` tool closes that gap: it loads the rendered `/view` page for a given snippet in a
headless browser and returns a PNG of it directly to the AI.

This matters for follow-up questions about appearance: "where does it peak?", "which bar is
tallest?", "what color is that slice?". Answering those from the raw data is often wrong,
because the rendered plot is not the same as the data: heatmaps can flip row order, axes get
inverted, categories get sorted, and histograms bin values. The screenshot is the only ground
truth for what the chart actually looks like.

```python
screenshot(snippet_id="abc123", width=1200, height=800, full_page=False)
```

If the returned image is too blurry, too small, or clipped to answer confidently, the AI is
instructed to fall back to reasoning from the code and data rather than guessing from a bad
picture.

---

## Why an Independent Panel Server?

Running visualizations in an independent subprocess provides several key benefits:

**Isolation**: If visualization code crashes or hangs, it does not affect the MCP server or the
AI assistant's session. Errors are captured and returned as structured messages.

**Decoupling**: The Panel server and MCP server are independent. You can restart, update, or
reconfigure the Panel server without restarting the MCP session (the MCP server will
auto-restart it).

**State Management**: The Panel server maintains its own SQLite database. Visualizations persist
across MCP sessions and are accessible even if the MCP server is stopped.

**Web Interface**: Running a dedicated Panel server allows full use of Panel's web framework:
reactive widgets, real-time updates, and multi-page navigation.

**Resource Control**: Long-running visualizations or large datasets run in a separate process
with their own memory space.

---

## Eager Startup and Auto-Restart

The Panel server starts **immediately** when `pls mcp` is called, not on the first `show`
invocation. This eliminates the 5–30 second startup penalty that would otherwise appear on
every first visualization request.

If the Panel server becomes unhealthy (crash, timeout, port conflict), the MCP server
automatically restarts it, up to `max_restarts` times (default: 3). A clean shutdown is
registered via `atexit` so the subprocess stops when the MCP server exits.

---

## Snippets and Execution Methods

A **snippet** is a stored code sample with metadata. Each snippet has:

- Unique ID and URL-friendly slug
- Python code
- Name and description
- Status: `pending`, `success`, or `error`
- Detected package imports and Panel extensions
- Execution method and timestamps

### Inline Method (Default)

Executes code like a Jupyter notebook cell. The last expression is captured and wrapped with
`pn.panel()` for display:

```python
import pandas as pd
df = pd.DataFrame({'x': [1, 2, 3], 'y': [4, 5, 6]})
df  # This expression is displayed
```

Best for: data exploration, quick charts, any Python object (DataFrames, plots, widgets).

### Server Method

Executes code that explicitly calls `.servable()` on Panel components. Multiple objects can be
served in a single snippet:

```python
import panel as pn

pn.extension()

slider = pn.widgets.IntSlider(name='Value', start=0, end=100)
pn.Column(slider, pn.bind(lambda x: f'{x}²  = {x**2}', slider)).servable()
```

Best for: complex interactive applications, multi-component dashboards.

### Module Namespace

Code executes inside a `types.ModuleType` namespace (registered in `sys.modules`). This ensures
Panel decorators like `@pn.cache` and `@pn.depends` work correctly, just as they do in Panel
application files.

---

## Database and URL Management

Snippets are stored in a SQLite database (default: `~/.panel-live-server/snippets/snippets.db`).
The database includes:

- All snippet metadata and code
- Execution results and error messages
- Full-text search index (FTS5) for finding snippets

URLs follow the pattern: `http://localhost:5077/view?id={snippet_id}`

In Jupyter environments (JupyterHub, Codespaces, Dev Containers), the MCP server detects the
proxy configuration and externalizes URLs so they are accessible from the user's browser.

---

## Browser Pages

| URL | Purpose |
|---|---|
| `/view?id=...` | Executes and renders a single snippet |
| `/feed` | Live-updating list of recent visualizations with inline previews |
| `/add` | Web form to create snippets manually |
| `/admin` | Management table: search, inspect, delete |

---

## Design Principles

1. **Simplicity**: One tool, minimal configuration, instant results
2. **Transparency**: Source code and metadata always visible in the UI
3. **Flexibility**: Works with any Python visualization library
4. **Persistence**: Snippets are saved and accessible across sessions
5. **Safety**: Isolated execution, visualization crashes cannot affect the AI session

---

## Related

- [Installation Tutorial](../tutorials/installation.md): create your first visualization
- [Configure the Server](../how-to/configure-server.md): ports, database, restart settings
- [API Reference](../reference/panel_live_server.md): full reference documentation
