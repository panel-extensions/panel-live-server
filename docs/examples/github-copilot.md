# Examples - MCP

Credits: These examples are heavily inspired by [panel-viz-mcp demos](https://github.com/AtharvaJaiswal005/panel-viz-mcp/blob/main/demos/DEMO_SCRIPT.md).

## Prerequisites

- VS Code with Github Copilot Chat
- panel-live-server MCP server with pydata dependencies installed and running
- skills from holoviz-mcp installed

### Bar chart

```text
Create a bar chart showing quarterly revenue:
Q1: 42000, Q2: 58000, Q3: 71000, Q4: 89000
```

<video controls autoplay muted loop style="width: 100%; max-width: 100%;">
  <source src="../assets/videos/vscode-copilot-bar-chart.mp4" type="video/mp4">
</video>

### Dashboard with filters

```text
Create a dashboard of this sales data with filters:

Region, Product, Sales, Quarter
North, Widget, 1200, Q1
North, Gadget, 800, Q1
South, Widget, 950, Q2
South, Gadget, 1100, Q2
East, Widget, 1400, Q3
East, Gadget, 600, Q3
West, Widget, 1050, Q4
West, Gadget, 900, Q4
```

### Streaming Chart

```text
Create a live streaming stock price chart starting at $150
```

<video controls autoplay muted loop style="width: 100%; max-width: 100%;">
  <source src="../assets/videos/vscode-copilot-streaming-chart.mp4" type="video/mp4">
</video>

### Multi-Chart View

```text
Show me a bar chart and scatter plot of this data side by side:
Name, Score, Hours
Alice, 85, 12
Bob, 92, 15
Carol, 78, 9
Dave, 95, 18
Eve, 88, 14
```

### Geographic Map (India)

```text
Create a geographic points map of India's major cities with this data:

City, Latitude, Longitude, Population_Millions, State, Region
Mumbai, 19.076, 72.878, 20.4, Maharashtra, West
Delhi, 28.614, 77.209, 16.8, Delhi, North
Bangalore, 12.972, 77.594, 8.4, Karnataka, South
Chennai, 13.083, 80.270, 7.1, Tamil Nadu, South
Kolkata, 22.573, 88.364, 4.5, West Bengal, East
Hyderabad, 17.385, 78.487, 6.8, Telangana, South
Ahmedabad, 23.023, 72.571, 5.6, Gujarat, West
Pune, 18.520, 73.856, 3.1, Maharashtra, West
Jaipur, 26.912, 75.787, 3.1, Rajasthan, North
Lucknow, 26.847, 80.947, 2.8, Uttar Pradesh, North
Kochi, 9.931, 76.267, 2.1, Kerala, South
Bhopal, 23.259, 77.413, 1.8, Madhya Pradesh, Central
Patna, 25.611, 85.144, 1.7, Bihar, East
Guwahati, 26.144, 91.736, 1.1, Assam, Northeast
Chandigarh, 30.734, 76.779, 1.1, Punjab, North
Visakhapatnam, 17.687, 83.218, 2.0, Andhra Pradesh, South
Indore, 22.720, 75.858, 1.9, Madhya Pradesh, Central
Coimbatore, 11.017, 76.956, 1.6, Tamil Nadu, South
Nagpur, 21.146, 79.089, 2.4, Maharashtra, Central
Surat, 21.170, 72.831, 4.5, Gujarat, West

Use Longitude as x-axis, Latitude as y-axis, color the points by Region. Title: "India - 20 Major Cities by Population". Make sure all 20 cities show as points on the map.
```

### Candlestick Chart (RELIANCE)

```text
Create a candlestick chart for RELIANCE stock with this OHLC data:

Date, Open, High, Low, Close
2026-02-01, 2890.50, 2945.30, 2875.10, 2920.75
2026-02-02, 2925.00, 2960.40, 2910.20, 2955.80
2026-02-03, 2950.30, 2985.60, 2935.00, 2940.15
2026-02-04, 2942.00, 2978.90, 2920.50, 2970.25
2026-02-05, 2968.40, 3010.75, 2958.30, 2995.60
2026-02-06, 2990.00, 3025.50, 2975.80, 2980.40
2026-02-07, 2985.20, 3015.30, 2960.10, 3008.75
2026-02-08, 3005.00, 3042.80, 2990.50, 3035.20
2026-02-09, 3030.50, 3055.40, 3010.25, 3020.60
2026-02-10, 3025.00, 3060.90, 3005.80, 3050.45
2026-02-11, 3048.30, 3075.20, 3030.00, 3040.15
2026-02-12, 3042.00, 3080.50, 3025.60, 3072.30
2026-02-13, 3070.00, 3095.40, 3050.20, 3060.80
2026-02-14, 3058.50, 3090.75, 3040.30, 3085.20
2026-02-15, 3082.00, 3110.50, 3065.80, 3070.40

Use Date as x-axis, Close as y-axis. Title: "RELIANCE - Daily OHLC (Feb 2026)"
```

### Candlestick Portfolio Dashboard

```text
Create a candlestick chart of RELIANCE stock with this OHLC data. Use Date as x-axis, Close as y-axis, color by Sector. Title it "RELIANCE Portfolio Analysis". Then open it in Panel as a full dashboard.

Date, Open, High, Low, Close, Volume, Sector, Action
2026-02-01, 2890.50, 2945.30, 2875.10, 2920.75, 4500000, Energy, buy
2026-02-02, 2925.00, 2960.40, 2910.20, 2955.80, 3800000, Energy, hold
2026-02-03, 2950.30, 2985.60, 2935.00, 2940.15, 5200000, Energy, sell
2026-02-04, 2942.00, 2978.90, 2920.50, 2970.25, 4100000, Energy, buy
2026-02-05, 2968.40, 3010.75, 2958.30, 2995.60, 6300000, Energy, buy
2026-02-06, 2990.00, 3025.50, 2975.80, 2980.40, 3900000, Energy, hold
2026-02-07, 2985.20, 3015.30, 2960.10, 3008.75, 4800000, Energy, buy
2026-02-08, 3005.00, 3042.80, 2990.50, 3035.20, 5500000, Energy, hold
2026-02-09, 3030.50, 3055.40, 3010.25, 3020.60, 3200000, Energy, sell
2026-02-10, 3025.00, 3060.90, 3005.80, 3050.45, 4700000, Energy, buy
2026-02-11, 3048.30, 3075.20, 3030.00, 3040.15, 4100000, Energy, hold
2026-02-12, 3042.00, 3080.50, 3025.60, 3072.30, 5800000, Energy, buy
2026-02-13, 3070.00, 3095.40, 3050.20, 3060.80, 3600000, Energy, sell
2026-02-14, 3058.50, 3090.75, 3040.30, 3085.20, 4900000, Energy, buy
2026-02-15, 3082.00, 3110.50, 3065.80, 3070.40, 4200000, Energy, hold
```

### Medical Image Viewer

```text
Use create_panel_app to build a medical image viewer. Load real brain MRI data with skimage.data.brain() which gives a (10, 256, 256) uint16 array. Normalize it to 0-1 float. Show 4 panels in a 2x2 pn.GridSpec layout that fills the screen without scrolling: top-left is the current brain slice as hv.Image with colorcet fire colormap (label "Axial - Slice N"), top-right is the same slice with colorcet kbc colormap (label "Enhanced View"), bottom-left is skimage.data.cell() normalized as hv.Image with colorcet gray colormap (label "Cell Microscopy"), bottom-right is skimage.data.human_mitosis() normalized as hv.Image with colorcet bmy colormap (label "Cell Division"). All 4 images: height=320, responsive=True, no axes (xaxis=None, yaxis=None), black bgcolor, hover tools. Sidebar must use a clean vertical pn.Column layout with proper spacing - put each widget on its own line with pn.pane.Markdown labels above each: "**Slice Navigation**" then slice_w IntSlider(name="Slice Index", start=0, end=9, value=5), then pn.layout.Divider(), then "**Colormap**" then cmap_w Select(name="Brain Colormap", options=["fire","bmy","kbc","gray","rainbow"], value="fire"), then pn.layout.Divider(), then "**Image Processing**" then brightness FloatSlider(name="Brightness", start=0.5, end=3.0, value=1.0, step=0.1), then contrast FloatSlider(name="Contrast", start=0.5, end=3.0, value=1.0, step=0.1). Apply brightness as multiply and contrast as power. GridSpec min_height=700 sizing_mode=stretch_both. No title banner markdown in main area. Dark theme, blue header accent #0ea5e9 header_background #0284c7. Title "Medical Image Viewer". Code must end with .servable().
```

### ML Model Evaluator

```text
Use create_panel_app to build an interactive ML model evaluation dashboard. Import panel, bokeh.plotting, bokeh.models, bokeh.palettes Blues9, numpy, pandas, and from sklearn import load_breast_cancer, train_test_split, RandomForestClassifier, confusion_matrix, roc_curve, auc, classification_report, accuracy_score.

Create a train_model function that loads breast cancer data, splits it, trains a RandomForestClassifier, and returns confusion matrix, fpr, tpr, roc_auc, classification report dict, accuracy, feature importances, and feature names.  <!-- codespell:ignore fpr tpr roc_auc -->

Create 4 chart builder functions that each return a Bokeh figure with dark styling (background_fill_color="#0a0a0a", border_fill_color="#0a0a0a", white title and axis text):

1. Confusion matrix: bokeh figure with x_range and y_range ["Malignant","Benign"], use p.rect for colored cells with Blues9 palette, p.text overlay showing counts in white 20pt bold. min_border_left=70.

2. ROC curve: bokeh figure with p.line for ROC in blue #3b82f6 and diagonal dashed gray reference line. Show AUC in title. Legend with transparent background and white text.

3. Feature importances: horizontal bar chart using p.hbar, orange #f97316, sorted descending, top N features. min_border_left=150 so names are visible. Shorten names to 20 chars.

4. Classification report: pandas DataFrame with Malignant, Benign, Macro Avg, Weighted Avg rows and Precision, Recall, F1, Support columns. Display as pn.widgets.Tabulator with theme="midnight".

Sidebar: Model Settings header, IntSlider for Trees (50-500 step 50), IntSlider for Max Depth (2-20), FloatSlider for Test Size (0.1-0.5), IntSlider for Top Features (5-20), Divider, green Retrain Model button, Divider, Metrics header, then 4 pn.indicators.Number showing Accuracy, Precision, Recall, F1 as percentages with font_size="24pt" and default_color="white".

The Retrain button callback calls train_model with current widget values, rebuilds all 4 charts, and updates indicator values.

Layout: pn.GridSpec(sizing_mode="stretch_both", min_height=700) with confusion matrix top-left, ROC top-right, features bottom-left, table bottom-right. Wrap in FastListTemplate with title "ML Model Evaluator", header_background="#7c3aed", theme="dark", theme_toggle=False. End with .servable().
```
