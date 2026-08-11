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
  │  HTTP  GET  /api/snippet
  │  HTTP  POST /api/snippet/edit
  │  HTTP  POST /api/screenshot
  │  HTTP  POST /api/evaluate
  │  HTTP  GET  /api/health
  ▼
pls serve, Panel Server (subprocess, port 5077)
  │  SQLite  ~/.panel-live-server/snippets/snippets.db
  ▼
Browser, /view  /feed  /add  /admin
```

**MCP Server** (`pls mcp`): Hosts the `show`, `screenshot`, `edit`, and `evaluate` MCP tools.
Starts the Panel server as a subprocess and manages its lifecycle. It never executes snippet code
itself — every tool forwards the code to the Panel server, which owns execution.

**Panel Server** (`pls serve`): Executes Python code and serves visualizations as web pages.
Exposes a REST API and four browser-accessible pages.

**Browser**: Displays visualizations and management interfaces.

---

## MCP Tools

The MCP server exposes four tools to the AI assistant, meant to be used together in a
typical session. They differ by *who receives what*:

| Tool | Returns | To whom |
|---|---|---|
| `show` | a live, interactive page | the user |
| `screenshot` | a PNG of the rendered page | the assistant |
| `edit` | the id of the changed code | the assistant |
| `evaluate` | text — stdout and the last expression | the assistant |

The **assistant** cannot install packages. It writes code against whatever is already in the
server environment, so the MCP server's instructions steer it toward **HoloViz packages**
(hvPlot, HoloViews, Panel) first, falling back only when HoloViz cannot do the job. The core
install also ships **Bokeh** (HoloViz's default backend) and the **ECharts** / **deck.gl** Panel
panes, which are always available with no extra package. (Other well-known libraries such as
Matplotlib, Plotly, seaborn, or Altair live in the optional `[pydata]` extra and may be absent.)
If an import is missing, `show` reports it and the assistant rewrites the code rather than
assuming availability.

**You** decide what is in that environment. `pls` runs in whichever environment you installed it
into and simply inherits whatever is there, so both the available packages *and their versions*
are determined by that environment rather than by Panel Live Server. You can add or upgrade
anything you like in it, using `pixi add --pypi <pkg>`, `uv tool install --with <pkg>`,
`conda install <pkg>`, or whatever manages that environment, and the assistant can then use it.
The `[pydata]` extra bundles a common set (Matplotlib, Plotly, seaborn, Altair, and more). See
[Add packages to the server environment](../tutorials/installation.md#add-packages-to-the-server-environment)
for the details, and run `pls list packages` to see what is installed and at which versions.

### `show`: validate, then render the visualization

The primary tool for turning code into a live visualization. Validation is built in: before
anything is stored or rendered, `show` runs a chain of static checks and, if any fail, returns
a quiet "Refining…" retry payload instead of a broken render — no separate `validate` call is
needed.

The checks, in order:

1. **Syntax**: `ast.parse()` catches syntax errors early
2. **Security**: ruff security rules plus a blocked-import list — a guardrail against
   plausible assistant mistakes, **not a sandbox** (see [Trust boundary](#trust-boundary))
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

- **code**: Python code to execute — omit when promoting a draft
- **name**: Human-readable title
- **description**: One-sentence explanation
- **method**: Execution method, `"inline"` (default) or `"server"`
- **zoom**: Initial zoom level, `25`, `50`, `75`, or `100`
- **draft_id**: Hand over a draft the assistant already rendered, instead of resending its code

**Promoting a draft.** When the assistant has been iterating with `screenshot`, the approved
version already exists on the server, already ran, and already rendered in a real browser.
`show(draft_id=...)` flips it out of draft state and returns its URL — no re-validation, no second
execution, and no need to resend the code. The alternative, `show(code=...)` with the snippet
pasted again, costs the whole snippet in output tokens and re-runs work that is already done. It
also removes a failure mode: promotion cannot introduce a difference between what the assistant
looked at and what the user receives.

The returned payload deliberately does **not** echo the code back. That echo was the largest
field in a message the assistant re-reads on every subsequent turn, spent to populate a panel the
user opens rarely. The App fetches the code from `GET /api/snippet?id=` instead, when the panel is
actually opened.

### `screenshot`: see the result, don't guess

`show` returns a live URL, but an AI assistant cannot open a browser to look at it. The
`screenshot` tool closes that gap: it loads a rendered `/view` page in a headless browser and
returns a PNG of it directly to the AI.

It takes either a `snippet_id`, a `draft_id`, or raw `code`:

```python
screenshot(snippet_id="abc123", width=1200, height=800)  # something already shown
screenshot(code="...", method="server")                  # a draft nobody has seen
screenshot(draft_id="def456")                            # that draft again, after an edit
```

**Reviewing a draft.** The `code` form renders the snippet and captures it while keeping it out
of the chat, out of the feed, and out of search. That gives the AI a private loop — render, look,
fix, look again — and `show` gets called once, at the end, on work that is actually finished.
Without it the AI has to call `show` to obtain a `snippet_id`, which means every half-baked
intermediate version is published to the user before the AI has even seen it.

The draft is **retained** rather than discarded, which is what lets `show(draft_id=...)` hand it
over later without storing and executing the same code a second time. Drafts are swept by age
(`draft_retention_hours`, default 24) rather than on the way out.

The draft is also deliberately *not* executed before the capture. Loading `/view` runs it and
stamps the row with a status and, on failure, a traceback — so the row is re-read afterwards and a
broken draft comes back as its traceback rather than as a picture of one. One execution per
draft, not two.

**Answering questions about appearance.** The `snippet_id` form handles follow-ups on something
the user already has: "where does it peak?", "which bar is tallest?", "what color is that
slice?". Answering those from the raw data is often wrong, because the rendered plot is not the
same as the data: heatmaps can flip row order, axes get inverted, categories get sorted, and
histograms bin values. The screenshot is the only ground truth for what the chart actually
looks like.

If the returned image is too blurry, too small, or clipped to answer confidently, the AI is
instructed to fall back to reasoning from the code and data rather than guessing from a bad
picture.

**What the render produced, not just how it looked.** A screenshot also carries back the text
side of the render, when there is any: anything the snippet wrote to stdout or stderr, and any
browser console messages or uncaught page errors observed during capture. Both are truncated,
and consecutive duplicate console lines are collapsed to `(xN)` — one failing tile prefetch can
otherwise log the same message hundreds of times and crowd out the message that explains it.

The console half matters more than it sounds. Bokeh reports layout and tile problems *only* to
the JavaScript console (`tile extent is not fully defined`, `could not set initial ranges`), and
a plot that fails for one of those reasons screenshots as an empty frame — visually identical to
a plot with no data, or one drawn off-screen. Without the console the picture cannot distinguish
them, which makes it the least informative evidence available at exactly the moment it is most
tempting to keep taking pictures.

### `edit`: change part of a snippet without resending it

A draft loop otherwise costs a full rewrite per turn. To change one colour in a 200-line snippet,
the assistant resends all 200 lines — every round. `edit` makes the output proportional to the
change rather than to the snippet:

```python
edit(snippet_id="abc123", old_str="color='blue'", new_str="color='red'")
```

`old_str` must occur exactly once. Zero matches and multiple matches are both refused rather than
guessed at, and an edit that would leave the code unparsable is rejected before anything is
written — finding out by launching a browser is the expensive way to learn about a missing
bracket.

**Drafts are edited in place. Shown snippets are forked.** Nothing changes underneath a user who
is already looking at it, so editing something already shown creates a *new* draft carrying the
change and returns its id; the live version does not move. Showing the fork adds a new entry to
the feed and leaves the previous one intact, which also means "go back to the old one" costs
nothing.

Refusing to edit shown snippets was the earlier design. It protected the same invariant, but it
cost a wasted call on the commonest shape there is — show, then "tweak that" — and pushed the
assistant back to resending the whole snippet, which is exactly what this tool exists to avoid.

The edited code is executed before the call returns, so the returned id is ready for
`show(draft_id=...)` directly. Two consequences: a change that breaks at runtime is refused with
its traceback rather than becoming a showable id, and a small tweak does not need a screenshot in
between purely to make the result promotable. Screenshotting remains worthwhile when the
assistant needs to *see* the change rather than just make it.

The response carries the id and a character count, never the code. Echoing the snippet back would
spend exactly what the edit just saved.

### `evaluate`: read a value without rendering anything

Not every question needs a picture. Does this option exist, what does this function return, what
columns does the DataFrame have, what range did Bokeh actually compute — those have textual
answers, and routing them through `screenshot` means launching Chromium and rendering the text
into an image purely so it can be read back out of one.

`evaluate` runs the code and returns what it printed plus the repr of its last expression:

```python
evaluate(code="import holoviews as hv; hv.render(plot).x_range.start")
# => -9243970.515473438
```

The point is *which environment* it runs in. An assistant usually has some shell of its own, but
not one with the plotting stack installed; the Panel server environment is the only place those
packages exist. `evaluate` is access to it without the browser.

Execution happens in the Panel server process, exactly as `/view` does, in a fresh throwaway
module per call — so evaluations cannot see each other's names and nothing is left in
`sys.modules`. Nothing is written to the database, so an evaluation can never reach the feed.

The tool description is deliberately fenced: `evaluate` must not be used to answer questions
about *appearance*. Recomputing where a peak sits or which bar is tallest from the raw data is
the specific mistake `screenshot` exists to prevent, since the rendered plot and the data
frequently disagree. Facts about objects go here; facts about pixels go to `screenshot`.

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
- Draft flag: held back from the feed and from search while the assistant is still iterating

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

**Code is stored verbatim.** Whatever the assistant sent is what lands in the database, character
for character. Formatting is applied when code is *read* for a human — by the feed's code tab, and
by `GET /api/snippet?id=` when the App's code panel is opened — rather than on the way in.

The reason is `edit`. It matches `old_str` against the stored text, and the assistant matches
against what it wrote. Reformatting between the two makes the edit miss for reasons nobody can
see: `ruff format` normalises quote style, so a snippet written with `'x'` is stored as `"x"` and
no single-quoted `old_str` can ever match it. Formatting at read time keeps the stored bytes
honest and still shows people tidy code.

One consequence worth knowing when debugging: the code panel shows *formatted* code, so it cannot
be used to inspect exactly what is stored. Query the database directly for that.

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

## Trust boundary

Panel Live Server exists to execute arbitrary Python. `show`, `screenshot`, and `evaluate` all
run supplied code in the Panel server process, with the full privileges of whoever started it —
the same reach as any script that user could run directly, including their files and the network.

The validation described under [`show`](#show-validate-then-render-the-visualization) is a
**guardrail, not a sandbox**. A blocked-import list and ruff's security rules catch the plausible
mistakes an assistant makes and refuse imports with no place in a visualization. They are static
checks on source text, and they are not a security boundary — treat them as a way to fail early
on obvious problems, never as containment.

Two consequences worth being explicit about:

- **The environment is the boundary.** Run `pls` only somewhere you would be content to run
  arbitrary code. The choice of environment is the actual security decision; nothing inside the
  server narrows it.
- **The port is unauthenticated.** Anyone who can reach `/api/snippet` or `/api/evaluate` can
  execute code in that environment. Keep it on a loopback interface or behind something that
  authenticates, and don't publish it to an untrusted network.

This is a deliberate design position rather than a gap: a local visualization server that
validated its way to safety would also refuse most of what makes it useful. The trade is stated
here so the deployment decision is made knowingly.

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
