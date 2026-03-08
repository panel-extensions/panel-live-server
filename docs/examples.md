# Examples

Copy-paste snippets for the most common visualization types. All examples use the default
`jupyter` method — the last expression is displayed automatically, just like a notebook cell.

Use the `panel` method (with `.servable()`) for multi-component dashboards; those examples
are in the [Panel dashboard](#panel-dashboard) and [DataFrame table](#dataframe-table) sections below.

!!! tip "Install the full PyData stack"
    ```bash
    pip install "panel-live-server[pydata]"
    ```
    This installs hvplot, plotly, altair, matplotlib, seaborn, polars, and more alongside
    Panel Live Server.

---

## Plotly

Interactive charts with hover tooltips, zoom, and pan out of the box.

### Bar chart

```python
import plotly.express as px

df = px.data.medals_long()
px.bar(df, x="nation", y="count", color="medal", barmode="group",
       title="Olympic medals by nation")
```

### Scatter plot

```python
import plotly.express as px

df = px.data.iris()
px.scatter(df, x="sepal_width", y="sepal_length", color="species",
           size="petal_length", hover_data=["petal_width"],
           title="Iris sepal dimensions")
```

### Time series

```python
import plotly.express as px

df = px.data.stocks()
px.line(df, x="date", y=["GOOG", "AAPL", "AMZN"],
        title="Stock prices over time")
```

---

## hvPlot / HoloViews

Quick `.hvplot()` on any pandas or polars DataFrame. Produces interactive Bokeh charts.

### Bar chart

```python
import pandas as pd
import hvplot.pandas

df = pd.DataFrame({
    "species":  ["Adelie", "Chinstrap", "Gentoo"],
    "count":    [152, 68, 124],
})
df.hvplot.bar(x="species", y="count", title="Penguin species counts",
              color="species", legend=False)
```

### Scatter plot

```python
import pandas as pd
import numpy as np
import hvplot.pandas

rng = np.random.default_rng(0)
df = pd.DataFrame({
    "x": rng.normal(size=200),
    "y": rng.normal(size=200),
    "group": rng.choice(["A", "B", "C"], size=200),
})
df.hvplot.scatter(x="x", y="y", by="group", title="Scatter by group")
```

### Line chart

```python
import pandas as pd
import numpy as np
import hvplot.pandas

dates = pd.date_range("2024-01-01", periods=90, freq="D")
df = pd.DataFrame({
    "date":  dates,
    "value": np.cumsum(np.random.default_rng(1).normal(size=90)),
})
df.hvplot.line(x="date", y="value", title="90-day cumulative return")
```

---

## Matplotlib / Seaborn

Static figures rendered as images. Seaborn's statistical plots are especially useful.

### Histogram (matplotlib)

```python
import matplotlib.pyplot as plt
import numpy as np

rng = np.random.default_rng(42)
data = rng.normal(loc=0, scale=1, size=1000)

fig, ax = plt.subplots()
ax.hist(data, bins=30, edgecolor="white")
ax.set(title="Normal distribution", xlabel="Value", ylabel="Count")
fig
```

### Distribution plot (seaborn)

```python
import seaborn as sns
import matplotlib.pyplot as plt

penguins = sns.load_dataset("penguins").dropna()

fig, ax = plt.subplots(figsize=(8, 4))
sns.histplot(penguins, x="flipper_length_mm", hue="species", kde=True, ax=ax)
ax.set_title("Flipper length distribution by species")
fig
```

### Heatmap (seaborn)

```python
import seaborn as sns
import matplotlib.pyplot as plt

flights = sns.load_dataset("flights").pivot(
    index="month", columns="year", values="passengers"
)
fig, ax = plt.subplots(figsize=(10, 6))
sns.heatmap(flights, annot=True, fmt="d", cmap="YlOrRd", ax=ax)
ax.set_title("Monthly airline passengers")
fig
```

---

## Panel widgets

Interactive widgets with reactive bindings. Uses the `jupyter` method — Panel's
`pn.panel()` wraps the bound function output automatically.

### Slider with live output

```python
import panel as pn

x_slider = pn.widgets.IntSlider(name="x", start=0, end=100, value=10)

def square(x):
    return f"{x} squared is **{x ** 2}**"

pn.Column(x_slider, pn.bind(pn.pane.Markdown, pn.bind(square, x_slider)))
```

### Select + plot

```python
import panel as pn
import pandas as pd
import hvplot.pandas

df = pd.DataFrame({
    "month":   ["Jan", "Feb", "Mar", "Apr", "May", "Jun"],
    "revenue": [120, 95, 180, 150, 210, 175],
    "cost":    [80,  70, 100, 110, 130, 120],
})

metric = pn.widgets.Select(name="Metric", options=["revenue", "cost"])

def plot(m):
    return df.hvplot.bar(x="month", y=m, title=f"Monthly {m}")

pn.Column(metric, pn.bind(plot, metric))
```

---

## Panel dashboard

A multi-component layout with a sidebar and reactive widgets. Use `method="panel"` and
call `.servable()` on the objects you want displayed.

```python
import panel as pn
import pandas as pd
import numpy as np
import hvplot.pandas

pn.extension()

rng = np.random.default_rng(0)
N = 200
df = pd.DataFrame({
    "x":     rng.normal(size=N),
    "y":     rng.normal(size=N),
    "group": rng.choice(["A", "B", "C"], size=N),
})

group_select = pn.widgets.CheckBoxGroup(
    name="Groups", value=["A", "B", "C"], options=["A", "B", "C"]
)
alpha_slider = pn.widgets.FloatSlider(name="Point opacity", start=0.1, end=1.0, value=0.7)

def scatter(groups, alpha):
    filtered = df[df["group"].isin(groups)] if groups else df.iloc[:0]
    return filtered.hvplot.scatter(x="x", y="y", by="group", alpha=alpha,
                                   width=500, height=400)

pn.template.FastListTemplate(
    title="Scatter Explorer",
    sidebar=[group_select, alpha_slider],
    main=[pn.bind(scatter, group_select, alpha_slider)],
).servable()
```

---

## DataFrame table

An interactive, sortable and filterable table using `pn.widgets.Tabulator`.
Use `method="panel"`.

```python
import panel as pn
import pandas as pd

pn.extension("tabulator")

df = pd.DataFrame({
    "name":    ["Alice", "Bob", "Carol", "Dave", "Eve"],
    "dept":    ["Eng", "Eng", "HR", "Finance", "HR"],
    "salary":  [95000, 88000, 72000, 81000, 69000],
    "rating":  [4.5, 3.8, 4.2, 4.0, 4.7],
})

pn.widgets.Tabulator(
    df,
    pagination="local",
    page_size=10,
    header_filters=True,
    sizing_mode="stretch_width",
).servable()
```
