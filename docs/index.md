# panel-live-server

[![CI](https://img.shields.io/github/actions/workflow/status/panel-extensions/panel-live-server/ci.yml?style=flat-square&branch=main)](https://github.com/panel-extensions/panel-live-server/actions/workflows/ci.yml)
[![conda-forge](https://img.shields.io/conda/vn/conda-forge/panel-live-server?logoColor=white&logo=conda-forge&style=flat-square)](https://prefix.dev/channels/conda-forge/packages/panel-live-server)
[![pypi](https://img.shields.io/pypi/v/panel-live-server.svg?logo=pypi&logoColor=white&style=flat-square)](https://pypi.org/project/panel-live-server)
[![python](https://img.shields.io/pypi/pyversions/panel-live-server?logoColor=white&logo=python&style=flat-square)](https://pypi.org/project/panel-live-server)

Building interactive data apps with Panel, hvPlot, or HoloViews usually means: write code,
start a server, open a browser, refresh, repeat. That loop adds up for quick experiments. And
if you are working inside an AI assistant like Claude or GitHub Copilot, asking it to help you
build a chart, there has been no clean way to actually see the result without leaving your tool.

**panel-live-server removes that friction.** Point an MCP-compatible AI assistant at a dataset
or describe the chart you want, and the result comes back rendered, live, and interactive,
right inside the chat. Or run it standalone from the terminal: submit code through a browser UI
and get a permanent URL back instantly.

It ships two interfaces, built on the same underlying Panel server:

- **MCP server** (`pls mcp`): connects to AI assistants over the Model Context Protocol, so they can render and inspect visualizations directly inside the chat
- **Standalone server** (`pls serve`): a web server you drive yourself, through a browser UI or REST API

Use whichever fits how you work, or run both.

---

## MCP Server: AI assistant integration

Give Claude, GitHub Copilot, Cursor, or any MCP-compatible AI assistant the ability to render
visualizations directly in your IDE, and to actually see what it just rendered. Two tools are
exposed:

- **`show`**: validates the code (syntax, security, package availability, Panel extensions) and then executes it, returning a live, interactive visualization — no manual setup and no separate validation step required. The AI is instructed to reach for HoloViz packages (hvPlot, HoloViews, Panel) first, falling back to other well-known libraries only when needed
- **`screenshot`**: renders a visualization and hands the picture back to the AI rather than to you. Point it at code the AI is still working on and it can check its own output privately, fixing and re-checking until it is right, so `show` only ever runs on the finished result. Point it at something already shown and it answers follow-up questions about how the chart looks by inspecting the actual image instead of guessing from raw data

<video controls autoplay muted loop style="width: 100%; max-width: 100%;">
  <source src="assets/videos/panel-live-server-showcase-mcp.mp4" type="video/mp4">
</video>

Install the package, then start the MCP server:

=== "pixi"

    ```bash
    pixi add --pypi "panel-live-server[pydata]"
    ```

=== "uv"

    ```bash
    uv tool install "panel-live-server[pydata]"
    ```

=== "pip"

    ```bash
    pip install "panel-live-server[pydata]"
    ```

```bash
pls mcp  # configure this command in Claude, Copilot, etc.
```

See the [Installation tutorial](tutorials/installation.md) for per-package-manager setup
details and connecting to your MCP client.

Ask your AI assistant:

> Please show a quick and beautiful Matplotlib trading dashboard

> Please show a basic, interactive Panel app with a slider.

> Now replace the text with a hvplot and show it.

> Please show the most beautiful matplotlib plot

The AI calls `show` to render it, and the visualization appears immediately in your chat
interface. If you then ask a follow-up question about how it looks, the AI can call
`screenshot` to look at the rendered image before answering.

---

## Standalone Server: browser UI and REST API

Start a local web server and create interactive visualizations through a browser UI or REST API.
Every snippet gets its own permanent URL.

<video controls autoplay muted loop style="width: 100%; max-width: 100%;">
  <source src="assets/videos/panel-live-server-showcase.mp4" type="video/mp4">
</video>

Install the package, then start the server:

=== "pixi"

    ```bash
    pixi add --pypi "panel-live-server[pydata]"
    ```

=== "uv"

    ```bash
    uv tool install "panel-live-server[pydata]"
    ```

=== "pip"

    ```bash
    pip install "panel-live-server[pydata]"
    ```

```bash
pls serve  # run this command in the terminal
```

Open [http://localhost:5077/add](http://localhost:5077/add) and submit any Python visualization:

```python
import pandas as pd
import hvplot.pandas

df = pd.DataFrame({'Product': ['A', 'B', 'C', 'D'], 'Sales': [120, 95, 180, 150]})
df.hvplot.bar(x='Product', y='Sales', title='Sales by Product')
```

Browse your visualizations at [/feed](http://localhost:5077/feed), manage them at
[/admin](http://localhost:5077/admin), and link directly to any individual chart at `/view?id=...`.

---

## Features

### Two execution methods

- **Inline** (default): the last expression is automatically displayed, just like a notebook cell
- **Server**: explicit `.servable()` calls for multi-component dashboards with reactive widgets

### Works with any Python visualization library

hvplot · plotly · altair · matplotlib · seaborn · holoviews · bokeh · vega · deckgl · and more

### Persistent storage

Every snippet is saved to a local SQLite database with full-text search. Visualizations survive
server restarts and are accessible by URL at any time.

### Robust subprocess management

The Panel server runs as a managed subprocess with health monitoring and automatic restart
(up to a configurable limit). Port conflicts and stale processes are handled automatically.

### Validate before you render

`show` runs four static checks (syntax, security, package availability, and Panel extension
declarations) automatically before it executes anything. Validation is built into the render
path, so there is no separate step to call and no double-validation overhead.

### See it, don't guess

A `screenshot` tool captures a PNG of an already-rendered visualization in a headless browser
and hands it to the AI. This lets the assistant correctly answer questions like "which bar is
tallest?" or "where does it peak?" by looking at the actual rendered output, since plots
routinely flip axes, reorder rows, or bin values differently than the raw data suggests.

### MCP App UI

When used with a compatible AI client, visualizations render inline with zoom controls
(25 / 50 / 75 / 100 %), one-click URL and code copying, and a loading indicator.

### REST API

```python
import requests

response = requests.post(
    "http://localhost:5077/api/snippet",
    json={"code": "1 + 1", "name": "Addition", "method": "inline"}
)
print(response.json()["url"])
```

### Works everywhere

Local, Jupyter, JupyterHub, VS Code Dev Containers, GitHub Codespaces: URLs are
automatically externalized via Jupyter Server Proxy when needed.

---

## Learn more

| | |
| --- | --- |
| [**Tutorial: Installation**](tutorials/installation.md) | Install `pls` and connect it to your AI assistant |
| [**Tutorial: Standalone Server**](tutorials/standalone-server.md) | Create, view, and manage visualizations from the browser |
| [**Tutorial: MCP Server**](tutorials/mcp-server.md) | Let an AI assistant create visualizations for you |
| [**How-to: Configure**](how-to/configure-server.md) | Custom ports, database path, MCP transport, Jupyter proxy |
| [**Explanation**](explanation/architecture.md) | Architecture, execution methods, design principles |
| [**Reference**](reference/panel_live_server.md) | Full API reference |
| [**Examples**](examples.md) | Copy-paste code snippets |
