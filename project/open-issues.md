# Open Issues

Keep this file up to date:

- Clearly mark the state of each issue (`Open` / `In Progress` / `Closed`)
- Move closed issues to `closed-issues.md`

---

## Manual Testing of MCP Show Flow

**Status:** Open

Run the example prompts in `docs/examples/github-copilot.md` manually (human or AI) against the
panel-live-server MCP tools. Compile feedback identifying prioritized bugs, issues, and feature
requests, and draft a plan for addressing them.

Latest run summary (Mar 10, 2026):

- 9/9 prompts rendered successfully.
- No hard runtime failures found.
- One concrete browser warning found (Choices `allowHTML` deprecation).
- Several UX/fidelity improvements identified; tracked as dedicated issues below.

---

## Fix Choices allowHTML Deprecation Warning

**Status:** Open

During manual prompt testing, filter-heavy dashboards surfaced browser warnings:

- "Deprecation warning: allowHTML will default to false in a future release..."

This should be fixed before upstream default changes alter behavior.

Acceptance criteria:

- No Choices deprecation warnings for stock dashboard/filter examples.
- Widget config is explicit and forward-compatible.

---

## Improve Single-Plot Layout (Reduce Blank Space)

**Status:** Open

Several jupyter-style `show` renders display large empty space below plots, which hurts demo
quality and first impressions.

Acceptance criteria:

- Single-plot outputs auto-fit more tightly by default.
- Existing multi-chart and dashboard layouts are not regressed.

---

## Improve Geographic Prompt Fidelity / Verifiability

**Status:** Open

Geographic prompts render, but users cannot easily verify requirements like "all points shown"
from the plot alone.

Acceptance criteria:

- Map outputs include stronger visual verification support (for example: better marker readability,
  fit bounds, or optional side table/count).
- Prompt requiring specific point counts can be validated visually.

---

## Improve Streaming Chart First-Load Experience

**Status:** Open

Streaming charts can appear static immediately after load (before enough updates accumulate).

Acceptance criteria:

- Users can perceive "live" behavior within the first seconds of load.
- Streaming examples remain deterministic and lightweight.

---

## Move Tool Guidance to a Skill

**Status:** Open

The `show` tool docstring currently embeds extensive workflow guidance (load skills, research docs,
validate before showing). This makes the workflow opinionated and hard for users to opt out of.

Move the guidance into a dedicated `panel-live-server` skill so users can load it explicitly,
keeping the tool itself lean and the workflow user-controlled.

---

## Add More Opinionated Visualization Tools

**Status:** Open

panel-viz-mcp offers specific, polished tools for common chart types with richer features and
bi-directional communication. Consider extending panel-live-server with targeted tools such as:

- `show_html` — render raw HTML
- `show_js` — Panel JSComponent (vanilla JS / web components)
- `show_react` — Panel ReactComponent (React/JSX)
- `show_echarts` — Panel ECharts pane
- `show_hvplot` — hvPlot plots

Alternatively, evaluate merging panel-viz-mcp into panel-live-server.

---

## Research panel-viz-mcp for Reuse

**Status:** Open

Deep-research the [panel-viz-mcp](https://github.com/AtharvaJaiswal005/panel-viz-mcp) project.
Identify ideas, code, and functionality worth reusing or merging into panel-live-server.

---

## Add HTTP Validation Endpoint (`/api/validate`)

**Status:** Open

Validation already exists in the MCP server (`validate` tool), but standalone HTTP routes only
expose `/api/snippet` and `/api/health`.

Add `/api/validate` so non-MCP clients can use the same validation path and error model.

Goals:

- Validation results are exposed via a `/api/validate` endpoint.
- Reuse existing validation layers and response schema as much as possible.
- Keep behavior aligned with MCP `validate` tool.

---

## Extend Validation Test Coverage (Targeted Gaps)

**Status:** In Progress

Core validation tests now exist in:

- `tests/test_validation.py`
- `tests/test_server.py` (validate/show behavior)

Focus this issue on targeted gaps, not baseline coverage.

Candidate additions:

- Regression tests for known deprecation-warning scenarios in dashboard widgets.
- End-to-end tests ensuring validation/output messaging stays consistent across MCP and HTTP (once
  `/api/validate` exists).
- Additional edge-case tests for layout/prompt-specific failures found during manual testing.

---

## Add Data Upload / File Passing to `show`

**Status:** Open

Code executed by `show` cannot currently access data files provided by the LLM. Options:

- Add a `data` kwarg to `show` accepting file content or a path, returning a temp filename the
  code can reference.
- Add a separate `upload(data)` tool that stores a file and returns a unique filename.
