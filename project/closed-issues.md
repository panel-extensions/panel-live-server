# Closed Issues

---

## [Closed] Update Documentation

**Scope**

- `docs/index.md` — content pass for the current feature set
- `docs/examples.md` — full expansion from a single snippet to seven sections

**Changes**

`docs/index.md`:
- MCP section updated to mention the `validate → show` workflow.
- New "Validate before you render" feature entry describing the four static checks and
  cached result sharing.
- Quick Start MCP tab updated to explain that errors are caught before rendering.

`docs/examples.md` rewritten with seven sections:
- **Plotly** — bar chart, scatter, time series (all `jupyter` method)
- **hvPlot / HoloViews** — bar, scatter, line on a pandas DataFrame
- **Matplotlib / Seaborn** — histogram, distribution plot with KDE, heatmap
- **Panel widgets** — slider with live output, select + reactive plot
- **Panel dashboard** — `FastListTemplate` with sidebar, checkbox group, and opacity slider (`panel` method)
- **DataFrame table** — sortable/filterable `Tabulator` (`panel` method)

---

---

## [Closed] Auto-Inject Panel Extensions in Jupyter Mode

**Symptom**

Submitting valid Plotly code in `jupyter` mode raised an `ExtensionError` before the
visualization was created:

```
Unexpected error: Required Panel extension(s) not loaded: 'plotly'.
Add pn.extension('plotly') to your code.
```

**Root cause**

`validate_extension_availability()` in `utils.py` called `find_extensions()` to detect
required extensions, then raised `ExtensionError` if the user hadn't declared them via
`pn.extension('plotly')`. Correct guard for `panel` method, but wrong for `jupyter` mode
where the user isn't expected to use Panel at all.

**Fix**

- `database.py` — extension validation (`validate_extension_availability`) is now skipped
  for `method="jupyter"`. Extensions are still stored on the snippet for later use.
- `view_page.py:create_view()` — calls `find_extensions(snippet.app)` and loads all
  detected extensions at the Panel session level via `pn.extension(*session_extensions)`
  before the page is served (always includes `"codeeditor"`). Extension injection was
  removed from inside `_execute_code()` — calling `pn.extension()` inside `exec()`'d code
  is too late for Panel's JS asset registration.

---

## [Closed] Don't Open MCP App on Validation Errors

**Symptom**

When code failed a pre-execution check, the `show` tool still opened the MCP App iframe
showing a blank/white panel instead of a clear error message.

**Root cause**

The `show` tool is decorated with `app=AppConfig(...)`, so every return value — including
error JSON — was rendered inside the MCP App iframe. Error paths (syntax, security,
missing package, extension error) all returned JSON strings instead of raising.

**Fix**

All error paths in `show()` now `raise ToolError(...)` instead of returning error JSON.
`ToolError` causes FastMCP to surface the error as plain text, never opening the App pane.
This includes the "warning" path where a snippet was created but had a runtime error —
previously it returned a URL that opened a blank iframe; now it raises immediately with the
runtime error message.

---

## [Closed] Add `validate` MCP Tool with Caching

**Motivation**

A separate `validate` tool gives the LLM an explicit step to check code before rendering,
producing clean structured output rather than a failed `show` call.

**Implementation**

- `server.py` — added `validate(code, method)` MCP tool (no `app=AppConfig`; always
  returns plain JSON). Added `_validation_cache: dict[tuple[str, str], dict]` and
  `_run_validation(code, method)` helper shared by both `validate` and `show`.
- `show()` — reads from `_validation_cache` (cache hit = no re-validation). Raises
  `ToolError` if `(code, method)` is not in the cache (enforce validate-first contract).
- FastMCP `instructions` updated with a numbered **VISUALIZATION TOOLING CONTRACT**
  (MUST/ALWAYS language).
- `show()` docstring updated with a **MANDATORY PRE-CONDITIONS** block.

**Validation layers**

1. Syntax — `ast.parse`
2. Security — ruff rules + blocked-import list
3. Package availability — all imports installed; error message explicitly forbids
   installing packages or changing the environment
4. Panel extensions — `pn.extension()` declared (panel method only; jupyter auto-injects)

---

## [Closed] Panel Server Orphaned After MCP Server Restart

**Symptom**

Restarting the MCP server (e.g. during development) left the Panel subprocess running with
old code. The new MCP session adopted the stale server instead of starting fresh.

**Root cause**

Python does not call `atexit` handlers on `SIGTERM`. When Claude killed the MCP process,
the Panel subprocess was never stopped, so it kept serving the previous version of the code.

**Fix**

- `server.py` — installed `signal.SIGTERM` handler (`_sigterm_handler`) that calls
  `_cleanup()` then re-raises SIGTERM for clean exit. Added `_cleaned_up` idempotency
  flag so `_cleanup()` is safe to call from both the signal handler and `atexit`.
- `manager.py:_try_recover_stale_server()` — if a healthy server is found on the port but
  `self.process is None` (not owned by this session), it is treated as an orphan: killed
  with SIGTERM (SIGKILL fallback), and `start()` launches a fresh subprocess. Previously
  any healthy server was adopted unconditionally.
