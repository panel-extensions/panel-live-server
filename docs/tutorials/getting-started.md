# Tutorial: Getting Started with Panel Live Server

In this tutorial you'll learn how to use Panel Live Server to create, view, and share interactive
Python visualizations. You'll explore both standalone usage and AI-assisted workflows. By the end,
you'll have created multiple visualizations and understand how to integrate Panel Live Server into
your development workflow.

## What You'll Learn

- Install and start Panel Live Server
- Create visualizations using the web interface (standalone mode)
- Create visualizations using AI assistants (MCP tool mode)
- View, browse, and manage your visualizations
- Understand execution methods (Jupyter vs Panel)
- Create visualizations programmatically using the REST API

## What You'll Need

- Python 3.12 or later
- Panel Live Server installed (`pip install panel-live-server`)
- Basic familiarity with Python and data visualization
- A web browser
- (Optional) An AI assistant configured with Panel Live Server for Part 2

## Understanding Panel Live Server

Panel Live Server has two components:

1. **Panel Server** (`pls serve`): A local web server that runs visualizations and provides a browser interface
2. **MCP Server** (`pls mcp`): An MCP server that lets AI assistants create visualizations via the `show` tool

You can use Panel Live Server in two ways:

- **Standalone**: Start the server manually and create visualizations via the web interface or REST API
- **With AI**: Start the MCP server — the Panel server starts automatically, and AI assistants use it via the `show` tool

---

## Part 1: Using Panel Live Server Standalone

### Step 1: Install Panel Live Server

```bash
pip install panel-live-server
```

### Step 2: Start the Server

Open your terminal and start Panel Live Server:

```bash
pls serve
```

You should see output like this:

```
Starting Panel Live Server...
Panel Live Server running at:

  Add:   http://localhost:5077/add
  Feed:  http://localhost:5077/feed
  Admin: http://localhost:5077/admin
```

Your server is now running. Keep this terminal open while you work through the tutorial.

!!! info "Custom Port or Host"
    ```bash
    pls serve --port 9999
    pls serve --host 0.0.0.0 --port 8080
    ```

### Step 3: Create Your First Visualization

Open your web browser and navigate to [http://localhost:5077/add](http://localhost:5077/add).
You'll see a form with a code editor.

Enter the following Python code:

```python
import pandas as pd
import hvplot.pandas

df = pd.DataFrame({
    'Product': ['A', 'B', 'C', 'D'],
    'Sales': [120, 95, 180, 150]
})

df.hvplot.bar(x='Product', y='Sales', title='Sales by Product')
```

Fill in the form fields:

- **Name**: "Product Sales Chart"
- **Description**: "An interactive bar chart showing sales by product"
- **Execution Method**: `jupyter` (default)

Click **Submit**. You'll see a success message with a link to your visualization.

!!! tip "Available Packages"
    Panel Live Server can use any package installed in your Python environment. Install additional
    libraries with `pip install package-name` — no server restart needed.

### Step 4: View Your Visualization

Click the link in the success message. You'll be taken to a URL like
`http://localhost:5077/view?id=abc123` where your chart is displayed.

The bar chart is interactive — hover over the bars to see tooltips.

!!! success
    Each visualization gets its own unique URL you can bookmark or share.

### Step 5: Browse Your Visualizations

Navigate to [http://localhost:5077/feed](http://localhost:5077/feed) to see a live-updating list
of your recent visualizations, including names, descriptions, and inline previews.

### Step 6: Manage Your Collection

Visit [http://localhost:5077/admin](http://localhost:5077/admin) for a table view of all
your snippets. From here you can search, filter, and delete visualizations.

### Step 7: Create Visualizations Programmatically

You can also create visualizations via the REST API. Create `script.py`:

```python
import requests

response = requests.post(
    "http://localhost:5077/api/snippet",
    headers={"Content-Type": "application/json"},
    json={
        "code": "a = 'Hello, Panel Live Server!'\na",
        "name": "Hello World",
        "method": "jupyter"
    }
)

print(f"Status: {response.status_code}")
print(f"Response: {response.json()}")
```

Run it:

```bash
python script.py
```

The response contains the URL of your new visualization.

!!! success
    You can now create visualizations through the web UI and programmatically via the REST API.

---

## Part 2: Using Panel Live Server with AI Assistants

Now let's use the MCP server to create visualizations through AI assistants with natural language.

### Prerequisites

An AI assistant (Claude, GitHub Copilot, etc.) configured to use the Panel Live Server MCP server.
See the [configure server guide](../how-to/configure-server.md) for setup instructions.

### Step 1: Start the MCP Server

In your IDE, start the Panel Live Server MCP server. The Panel server starts automatically.

!!! note
    If you're running the standalone server from Part 1, stop it with `CTRL+C` first.

### Step 2: Create Your First AI-Assisted Visualization

Open your AI assistant and ask:

> My dataset is penguins.csv. What is the distribution of the 'species' column? Use the show tool.

Your AI assistant will use the `show` tool and respond with:

```
Visualization created successfully!
View at: http://localhost:5077/view?id=...
```

Click the URL to see an interactive bar chart showing penguin species counts.

### Step 3: Explore Relationships with Scatter Plots

Ask your AI:

> Show me a scatter plot of 'flipper_length_mm' vs 'body_mass_g'

The AI creates a scatter plot with interactive tooltips, zoom, and pan.

### Step 4: Build Interactive Dashboards

For advanced use cases:

> Create an interactive dashboard for the penguins dataset with dropdown filters for species and island.

The AI will produce a Panel app with reactive widgets — dropdowns that filter the chart in real time.

### Step 5: Refine Your Visualizations

If the result isn't what you expected, continue the conversation:

- "Color the points by species"
- "Add a trend line to the scatter plot"
- "Show only penguins with body mass greater than 4000g"
- "Display these two charts side by side"

The AI iterates on your work, creating new visualizations that build on previous ones.

---

## Understanding Execution Methods

### Jupyter Method (Default)

Executes code like a Jupyter notebook cell — the last expression is automatically displayed:

```python
import pandas as pd
df = pd.DataFrame({'x': [1, 2, 3], 'y': [4, 5, 6]})
df  # This is displayed
```

Use this for data exploration, quick charts, and simple visualizations.

### Panel Method

For Panel dashboard applications with explicit `.servable()` calls:

```python
import panel as pn

pn.extension()

pn.Column(
    pn.pane.Markdown("# My Dashboard"),
    pn.widgets.Button(name="Click me")
).servable()
```

Use this for complex, interactive applications with multiple components.

---

## Understanding Storage

All visualizations are stored in a local SQLite database:

```
~/.panel-live-server/snippets/snippets.db
```

The database stores your Python code, execution results, metadata (names, descriptions,
timestamps), and detected packages.

!!! tip "Custom Database Location"
    Set `PANEL_LIVE_SERVER_DB_PATH` before starting the server to use a custom location.

---

## Checking Server Status

```bash
pls status
```

This queries the health endpoint and reports whether the server is running.

---

## Troubleshooting

### ModuleNotFoundError

Install the missing package — no server restart required:

```bash
pip install package-name
```

### Server Not Available (MCP mode)

Verify the MCP server is running and check its startup logs for "Panel server started successfully".

### Visualization Shows an Error

Ask your AI to fix it based on the error message shown, or start with a simpler visualization to
confirm the system is working.

---

## What You've Learned

- Start Panel Live Server standalone or via MCP
- Create visualizations using the web interface, REST API, and AI assistants
- View, browse, and manage your visualizations
- Use Jupyter and Panel execution methods
- Build interactive dashboards with natural language

## Next Steps

- **[Architecture](../explanation/architecture.md)** — understand how the server, MCP, and browser work together
- **[Configure the server](../how-to/configure-server.md)** — customize ports, host, database path, and restart limits
- **[Examples](../examples.md)** — quick code snippets to copy and adapt
