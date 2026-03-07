# Validation

## Goal

Every snippet entering the system — via the MCP `show` tool, REST API (`POST /api/snippet`),
or the `/add` web form — must pass the same validation pipeline before being stored.
The chokepoint is `database.py::create_visualization`, which all three paths call.

Validated, formatted code is stored in the database. The `copy code` button and feed always
show clean, safe, working code.

---

## Validation Pipeline

Five layers, executed in order. Layers 1–4 block submission (raise exceptions).
Layer 5 runs with a timeout and stores the result as `status="error"` but does not block.

### Layer 1 — Syntax (`ast_check`)
Use `ast.parse()` to catch syntax errors before doing anything else.
Returns a human-readable error string with line/column info, or `None`.
`create_visualization` raises `SyntaxError` on failure.

### Layer 2 — Security (`ruff_check`)
Static analysis via `ruff --select <rules>`. Raises `SecurityError` on any violation.
ruff is a **required dependency**.

Rules enforced:
- `F821` — undefined name
- `S102` — `exec()`
- `S307` — `eval()`
- `S301` — `pickle.loads()`
- `S602` — `subprocess` with `shell=True`
- `S605` — `os.system()` / `os.popen()`
- `S608` — SQL injection via string formatting
- `S103`, `S104`, `S108`, `S113`, `S202`, `S302`, `S306`, `S310`, `S323`, `S501`, `S506`

If ruff is not found at runtime: skip silently (log a warning), do not block.

`SecurityError` is a custom exception defined in `validation.py` so callers can catch it
specifically and return a clear 400 response.

### Layer 3 — Package availability (`check_packages`)
Parse all top-level imports via AST. For each, call `importlib.util.find_spec()`.
Skip stdlib modules (`sys.stdlib_module_names`). Raises `ValueError` on first missing package.

Import name → PyPI install name mapping (for user-friendly error messages):

```python
IMPORT_TO_PACKAGE: dict[str, str] = {
    "PIL":     "Pillow",          # import PIL  →  pip install Pillow
    "sklearn": "scikit-learn",
    "cv2":     "opencv-python",
    "skimage": "scikit-image",
    "bs4":     "beautifulsoup4",
    "yaml":    "PyYAML",
    "dateutil":"python-dateutil",
    "dotenv":  "python-dotenv",
    "gi":      "PyGObject",
    "wx":      "wxPython",
    "Crypto":  "pycryptodome",
    "OpenSSL": "pyOpenSSL",
    "usb":     "pyusb",
    "serial":  "pyserial",
    "magic":   "python-magic",
    "attr":    "attrs",
}
```

Note: PIL and Pillow are not duplicated — PIL is the import name, Pillow is the install name.

Error message format (prompts the LLM to use `list_packages`):
```
Package 'Pillow' is not installed in this environment.
Use the list_packages tool to discover what is available, then rewrite using an installed library.
```

### Layer 4 — Panel extension availability (`validate_extension_availability`)
Check that extensions inferred from the code (plotly, vega/altair, tabulator, etc.)
are declared in a `pn.extension(...)` call. Raises `ExtensionError`.
Source: `utils.py::validate_extension_availability` (existing).

### Layer 5 — Runtime execution (`validate_code`)
Execute the code in an isolated `types.ModuleType` namespace in a **separate thread**
with a 30-second timeout. This prevents hanging code from blocking the request indefinitely.

Note: Python threads cannot be forcibly killed — on timeout the thread continues running
as a daemon until the process exits. True isolation requires a subprocess; this is a
future improvement if needed.

On error: store snippet with `status="error"` and `error_message=<traceback>`.
On timeout: store with `status="error"` and `error_message="Code execution timed out (30s)"`.
Does not block submission (snippet is always stored after Layers 1–4 pass).

---

## Formatting

After Layers 1–4 pass, format the code with `ruff format` before Layer 5 and before storage.
The DB always holds clean, formatted code.

If ruff is not installed: store the original code unchanged (ruff is required, so this is a fallback).

---

## Code Organisation

### New: `src/panel_live_server/validation.py`
Derived from `code_validation.py` (project root). Pyodide-specific code is **removed**:
- No `pyodide_check()`, no `_ForbiddenPatternVisitor`
- No `PYODIDE_BUILTINS`

Contains:
- `SecurityError` — custom exception
- `IMPORT_TO_PACKAGE` — import→package name mapping
- `ast_check(code) -> str | None`
- `ruff_check(code) -> None` — raises `SecurityError` on violations, returns None if clean
- `check_packages(code) -> str | None`
- `ruff_format(code) -> str`

### Updated: `src/panel_live_server/utils.py`
- `validate_code(code) -> str` — runs `execute_in_module` in a `ThreadPoolExecutor`
  with 30s timeout; returns error string or `""`
- Remove `validate_extension_availability` call from inside `validate_code`
  (it is now Layer 4, called explicitly in `create_visualization`)
- All other functions unchanged

### Updated: `src/panel_live_server/database.py::create_visualization`
Calls the full pipeline:
```python
# Layer 1
if err := ast_check(app):
    raise SyntaxError(err)
# Layer 2
ruff_check(app)                    # raises SecurityError
# Layer 3
if err := check_packages(app):
    raise ValueError(err)
# Layer 4
validate_extension_availability(app)  # raises ExtensionError
# Format
app = ruff_format(app)
# Layer 5 (threaded, non-blocking-ish)
validation_result = validate_code(app)
```

Remove the old `ast.parse(app)` direct call and `.show(` string check.

### Updated: `src/panel_live_server/endpoints.py`
Add `SecurityError` to the exception handler alongside `SyntaxError` and `ValueError`.
Returns HTTP 400 with `{"error": "SecurityError", "message": ...}`.

### Updated: `pyproject.toml` and `pixi.toml`
Add `ruff` as a required runtime dependency (not just dev/lint).

---

## Error Messages for the LLM

The `show` tool returns `status="error"` with `message` and `recovery` fields:

- **Syntax**: `"Syntax error: expected ':' (line 3, col 8)"`
- **Security**: `"Security violation: exec() is not allowed (line 5). Rewrite without this pattern."`
- **Missing package**: `"Package 'scikit-learn' is not installed. Use the list_packages tool to discover what is available, then rewrite using an installed library."`
- **Missing extension**: `"Required Panel extension 'tabulator' not loaded. Add pn.extension('tabulator') to your code."`
- **Runtime error**: traceback string stored in `error_message`

---

## Testing

- `tests/test_validation.py` (new — adapted from `test_code_validation.py`)
  - Remove all `pyodide_check` tests
  - Keep `ast_check` tests
  - Update `ruff_check` tests: expect `SecurityError` raised, not string returned
  - Add `check_packages` tests:
    - installed package passes (`numpy`, `panel`)
    - fake package fails, message contains package name + "list_packages"
    - stdlib not flagged (`os`, `json`, `sys`)
    - aliased import works (`import numpy as np`)
    - `from X import Y` extracts `X`
    - mapping applied: `import sklearn` → error mentions `scikit-learn`
  - Keep `ruff_format` tests
- `test_code_validation.py` (root) — **delete** once tests pass
- Add integration test: `create_visualization` rejects at correct layer

---

## What to Drop / Not Adopt from panel-viz-mcp

panel-viz-mcp uses an allowlist (`_ALLOWED_MODULES`) that only permits panel, bokeh, numpy, etc.
Too restrictive for panel-live-server which supports arbitrary Python.
We use a blocklist (security rules) + availability check instead.
