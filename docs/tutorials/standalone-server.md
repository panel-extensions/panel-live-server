# Tutorial: Visualizations with the Standalone Server

In this tutorial you'll use `pls serve` to create, view, and manage interactive Python
visualizations through a browser interface. By the end, you'll have created several visualizations
and know how to browse and manage them.

## What You'll Need

- Panel Live Server installed — see [Installation](installation.md)
- A web browser

---

## Step 1: Start the server

```bash
pls serve
```

You should see:

```
Panel Live Server running at:

  Add:   http://localhost:5077/add
  Feed:  http://localhost:5077/feed
  Admin: http://localhost:5077/admin
```

Keep this terminal open for the rest of the tutorial.

!!! info "Custom port or host"
    ```bash
    pls serve --port 9999
    pls serve --host 0.0.0.0 --port 8080
    ```

---

## Step 2: Create your first visualization

Open [http://localhost:5077/add](http://localhost:5077/add) in your browser. You'll see a form
with a code editor.

Enter this code:

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

Click **Submit**. You'll see a success message with a link to your new visualization.

!!! success
    Each visualization gets its own unique URL you can bookmark or share.

---

## Step 3: View your visualization

Click the link in the success message. You'll be taken to a URL like
`http://localhost:5077/view?id=abc123`. Your interactive bar chart is running live —
hover over the bars to see the tooltips.

---

## Step 4: Try a Panel application

The `panel` execution method lets you serve a full Panel app with reactive widgets. Go back to
[/add](http://localhost:5077/add), clear the editor, and enter:

```python
import panel as pn

pn.extension()

slider = pn.widgets.IntSlider(name='x', start=0, end=100, value=10)

pn.Column(
    slider,
    pn.bind(lambda x: f'{x} squared is **{x**2}**', slider)
).servable()
```

- **Name**: "Square Calculator"
- **Description**: "A slider that computes its square"
- **Execution Method**: `panel`

Click **Submit** and open the link. Drag the slider — the result updates in real time.

---

## Step 5: Browse your visualizations

Navigate to [http://localhost:5077/feed](http://localhost:5077/feed). You'll see a live-updating
list of your recent visualizations with inline previews, names, descriptions, and direct links.

---

## Step 6: Manage your collection

Visit [http://localhost:5077/admin](http://localhost:5077/admin) for a table view of all your
snippets. From here you can search, inspect the code, and delete visualizations you no longer need.

---

## Step 7: Create a visualization via the REST API

Panel Live Server exposes a REST API, useful for automation and scripting. Create `script.py`:

```python
import requests

response = requests.post(
    "http://localhost:5077/api/snippet",
    json={
        "code": "msg = 'Hello, Panel Live Server!'\nmsg",
        "name": "Hello World",
        "method": "jupyter",
    }
)

data = response.json()
print(f"Status:  {response.status_code}")
print(f"View at: {data['url']}")
```

Run it:

```bash
python script.py
```

Open the printed URL to see your visualization.

---

## Understanding execution methods

### Jupyter (default)

The last expression in the code is automatically captured and displayed — just like a notebook cell:

```python
import pandas as pd
df = pd.DataFrame({'x': [1, 2, 3], 'y': [4, 5, 6]})
df  # This is displayed
```

Use this for data exploration, quick charts, DataFrames, and any Python object.

### Panel

Code that explicitly calls `.servable()` on Panel components. Multiple objects can be served:

```python
import panel as pn

pn.extension()

pn.Column(
    pn.pane.Markdown("# My Dashboard"),
    pn.widgets.Button(name="Click me"),
).servable()
```

Use this for complex, interactive applications with multiple components and reactive state.

---

## Checking server status

In a new terminal:

```bash
pls status
```

This queries the health endpoint and reports whether the server is running.

---

## What You've Learned

- Start `pls serve` and create visualizations via the web UI
- Use both the `jupyter` and `panel` execution methods
- Browse visualizations at `/feed` and manage them at `/admin`
- Create visualizations programmatically via the REST API

## Next Steps

- **[Use the MCP server](mcp-server.md)** — enable AI assistants to create visualizations for you
- **[Configure the server](../how-to/configure-server.md)** — custom ports, database path, and more
- **[Architecture](../explanation/architecture.md)** — understand how it all fits together
