# Tutorial: Visualizations with the MCP Server

In this tutorial you'll configure the Panel Live Server MCP server so that an AI assistant can
create interactive visualizations on your behalf using natural language. By the end, you'll have
asked an AI to produce a chart and seen it rendered live in your IDE.

## What You'll Need

- Panel Live Server installed, see [Installation](installation.md)
- Familiarity with snippets and execution methods, see [Standalone Server](standalone-server.md)
- An MCP-compatible AI assistant: Claude Code, Claude Desktop, GitHub Copilot (VS Code), or similar

---

## Step 1: Add Panel Live Server to your MCP configuration

See [Installation → Connect to your MCP client](installation.md#connect-to-your-mcp-client) for
the full setup instructions for VS Code, Cursor, Claude Desktop, Claude Code, and claude.ai.

!!! note
    When the MCP server starts, it automatically starts the Panel server in the background.
    You do not need to run `pls serve` separately. A standalone `pls serve` launched from the same environment resolves to the same per-environment port, so it shares that server rather than colliding. Run `pls status` to see the address.

---

## Step 2: Verify the connection

Ask your AI assistant:

> List your available MCP tools.

You should see two tools in the response:

- `show`: validates the code, renders the visualization, and returns a live URL
- `screenshot`: takes a picture of an already-rendered visualization so the AI can answer
  questions about how it looks

---

## Step 3: Create your first AI-assisted visualization

Download the [Palmer Penguins dataset](https://raw.githubusercontent.com/mcnakhaee/palmerpenguins/master/palmerpenguins/data/penguins.csv)
and save it as `penguins.csv`. Then ask your AI:

> My dataset is penguins.csv. Show the distribution of the 'species' column as an interactive bar chart. Use the show tool.

Your AI will call `show`, which validates the code and then renders it in one step.
You'll see a response like:

```
Visualization created successfully!
View at: http://localhost:5077/view?id=...
```

Click the URL (or the inline MCP App panel if your client supports it) to see the chart.

!!! note
    Inline preview support depends on the MCP client. Some clients permit the embedded iframe,
    while others block `localhost` origins and require opening the visualization in your browser.

!!! tip "Prompting tips"
    Mentioning the `show` tool explicitly ("use the show tool") ensures the AI uses it rather
    than describing the code. In VS Code you can reference it as `#show`.

---

## Step 4: Explore relationships

Continue the conversation:

> Show me a scatter plot of 'flipper_length_mm' vs 'body_mass_g', colored by species.

The AI will produce a new visualization with a color-coded scatter plot, interactive tooltips,
zoom, and pan.

---

## Step 5: Ask a follow-up question about how it looks

Now ask something that can only be answered by looking at the chart, not by reading the code:

> Which species has the widest spread of body mass in that scatter plot?

The AI cannot open a browser, so to answer correctly it calls `screenshot` on the snippet it
just created, gets back a picture of the rendered chart, and reads the answer off the image.

!!! note "Why this matters"
    Plots are not the same as the raw data: heatmaps can flip row order, axes get inverted,
    categories get sorted, and histograms bin values. Reasoning from the code alone often gives
    a different answer than what the chart actually shows. `screenshot` lets the AI check the
    real rendered output instead of guessing.

---

## Step 6: Build an interactive dashboard

Ask the AI to create a full Panel application:

> Create an interactive dashboard for the penguins dataset with a dropdown to filter by species and an island selector. Show a scatter plot that updates when the filters change.

The AI will use the `server` execution method and produce a reactive Panel app with widgets.
The dashboard updates in real time as you interact with it.

---

## Step 7: Iterate

If the result isn't what you expected, continue the conversation:

- "Color the points by island instead"
- "Add a trend line"
- "Show only penguins with body mass greater than 4000g"
- "Display the scatter plot and a histogram side by side"

Each message creates a new visualization. Previous ones remain accessible at their URLs.

---

## Step 8: Check what packages are available

The AI cannot install packages itself, and is instructed to prefer HoloViz packages (hvPlot,
HoloViews, Panel) and fall back to other well-known libraries only when needed — so you rarely
have to think about this. To inspect the environment yourself, use the `pls list packages` CLI
command in a terminal:

```bash
pls list packages          # list everything installed
pls list packages plotly   # filter by name
```

If a package you need is missing, see [Installation](installation.md#add-packages-to-the-server-environment)
for how to add it with `--with`.

---

## How it works

A typical AI-assisted session looks like this:

1. The AI calls `show`, which first validates the code (syntax, security, package
   availability, Panel extensions) and then sends it to the Panel server via the REST API
2. The Panel server stores and executes the snippet, returning a URL
3. The URL is shown to you, click it to open the live visualization
4. If you ask a question about how the result looks, the AI calls `screenshot` to see it
   before answering

See [Architecture](../explanation/architecture.md) for the full picture.

---

## Troubleshooting

### `show` tool is not available

Verify the MCP server started successfully. Check your AI client's MCP server logs for startup
errors. If `pls` is not on PATH inside the MCP process, use the full path:

```json
{ "command": "/home/user/.local/bin/pls", "args": ["mcp"] }
```

### Visualization shows an error

The error message is returned to the AI. Ask it to fix the issue, it has the full error context.
Or start with a simpler snippet to confirm the server is working:

> Show `1 + 1` using the show tool.

### Claude Desktop Not Showing the Visualization

Claude Desktop and Cowork restrict which origins their iframes may load, and `localhost` is
not among them, so neither can preview a visualization in the chat. Both show an
**Open in browser ↗** button instead. Click it, or open the returned
`http://localhost:5077/view?id=...` URL yourself, and the visualization runs there with full
interactivity against the live server.

If a client console logs an error like:

```text
Framing 'http://localhost:5077/' violates the following Content Security Policy directive: "frame-src 'self' blob: data:".
```

then the visualization URL is valid and the host simply refused to frame it. Open it in your
browser instead.

### Package not found in server environment

The server runs in an isolated uv tool environment. Install missing packages as described in
[Installation](installation.md#add-packages-to-the-server-environment).

### `screenshot` fails with a Playwright error

The `screenshot` tool needs the Chromium browser that Playwright manages, which is
not installed automatically. Install it once with:

```bash
pls install-browser
```

This downloads Chromium into the same environment that runs `pls`. See
[Installation → Enable the screenshot tool](installation.md#enable-the-screenshot-tool)
for the per-installer command.

---

## What You've Learned

- Configure the Panel Live Server MCP server for your AI assistant
- Ask the AI to create visualizations using natural language
- Ask a follow-up question about a visualization's appearance and have the AI check with `screenshot`
- Iterate on visualizations through conversation
- Check available packages with the `pls list packages` CLI command

## Next Steps

- **[Configure the server](../how-to/configure-server.md)**: custom port, transport, Jupyter proxy
- **[Architecture](../explanation/architecture.md)**: understand the MCP + Panel server design
- **[Examples](../examples.md)**: copy-paste snippets to try with your AI
