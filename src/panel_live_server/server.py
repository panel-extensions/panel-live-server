"""Panel Live Server - MCP Server.

A standalone MCP server that provides the `show` tool
for executing Python code and rendering visualizations via a Panel web server.
"""

import asyncio
import atexit
import json
import logging
import os
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastmcp import Context
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.apps import AppConfig
from fastmcp.server.apps import ResourceCSP
from fastmcp.tools.tool import ToolResult
from fastmcp.utilities.types import Image
from mcp.types import TextContent

from panel_live_server import diagnostics
from panel_live_server.client import BROWSER_UNAVAILABLE_PREFIX
from panel_live_server.client import DisplayClient
from panel_live_server.config import get_config
from panel_live_server.manager import PanelServerManager
from panel_live_server.prompts import SCREENSHOT
from panel_live_server.prompts import render_instructions
from panel_live_server.prompts import render_prompt
from panel_live_server.screenshot import is_browser_installed
from panel_live_server.utils import ExtensionError
from panel_live_server.utils import validate_extension_availability
from panel_live_server.validation import SecurityError
from panel_live_server.validation import ValidationError
from panel_live_server.validation import ast_check
from panel_live_server.validation import check_packages
from panel_live_server.validation import ruff_check

logger = logging.getLogger(__name__)

SHOW_RESOURCE_URI = "ui://panel-live-server/show.html"
SHOW_TEMPLATE_PATH = Path(__file__).parent / "templates" / "show.html"

# Global instances
_manager: PanelServerManager | None = None
_client: DisplayClient | None = None

# Validation cache: (code, method) → result dict. Session-scoped; reset on restart.
_validation_cache: dict[tuple[str, str], dict] = {}


def _run_validation(code: str, method: str) -> dict:
    """Run static validation layers and cache the result by (code, method).

    Checks (in order):
    1. Syntax — ``ast.parse``
    2. Security — ruff rules + blocked-import list
    3. Package availability — all imports must be installed
    4. Panel extensions — declared via ``pn.extension()`` (``server`` method only)

    Parameters
    ----------
    code : str
        Python code to validate.
    method : str
        Execution method (``"inline"`` or ``"server"``).

    Returns
    -------
    dict
        ``{"valid": True}`` on success, or
        ``{"valid": False, "layer": str, "message": str}`` on the first failure.
    """
    key = (code, method)
    if key in _validation_cache:
        return _validation_cache[key]

    result: dict = {}

    if err := ast_check(code):
        result = {"valid": False, "layer": "syntax", "message": err}
    else:
        try:
            ruff_check(code)
        except SecurityError as e:
            result = {"valid": False, "layer": "security", "message": str(e)}

    if not result:
        if err := check_packages(code):
            result = {"valid": False, "layer": "packages", "message": err}

    if not result and method == "server":
        try:
            validate_extension_availability(code)
        except ExtensionError as e:
            result = {"valid": False, "layer": "extensions", "message": str(e)}

    # method="server" renders only .servable() objects; a bare-expression ending renders empty, so catch it statically and retry rather than show a blank box.
    if not result and method == "server" and ".servable()" not in code:
        result = {
            "valid": False,
            "layer": "servable",
            "message": (
                "method='server' only displays objects marked with .servable(), "
                "but the code calls it nowhere. Either mark the app servable "
                "(e.g. `layout.servable()`), or call show() with method='inline' "
                "and end the code with the object as the last expression."
            ),
        }

    if not result:
        result = {"valid": True}

    _validation_cache[key] = result
    return result


def _brief_error(error_detail: str) -> str:
    """Return the one-line gist of *error_detail* for the failure strip.

    The last non-empty line of a traceback is the exception itself
    (``NameError: name 'x' is not defined``), which is the part worth showing on
    a single line. Single-line validation messages are returned as-is. The full
    text always remains available in the payload's ``error_message``.
    """
    lines = [line.strip() for line in (error_detail or "").strip().splitlines() if line.strip()]
    if not lines:
        return ""
    brief = lines[-1]
    max_len = get_config().brief_error_max_len
    if len(brief) > max_len:
        brief = brief[: max_len - 1] + "…"
    return brief


def _estimate_tokens(chars: int) -> int:
    """Estimate the token cost of *chars* characters of payload text."""
    # Prose averages ~4 chars per token; a rough figure, as no tokenizer is bundled.
    return round(chars / get_config().chars_per_token)


def _attach_token_count(payload: dict) -> None:
    """Add a ``tokens`` estimate to *payload*, in place (issue #45).

    A ``show()`` result is a message in the conversation, so every byte is read
    by the model. The count covers the payload as delivered, including this
    field's own framing.
    """
    body_chars = len(json.dumps(payload))
    # Count the field about to be added so the badge reports what the model receives; a wide placeholder keeps the self-reference from rounding itself away.
    framing_chars = len(json.dumps({"tokens": 9_999_999}))
    payload["tokens"] = _estimate_tokens(body_chars + framing_chars)


def _retry_payload(
    *,
    name: str,
    description: str,
    method: str,
    zoom: int,
    layer: str,
    error_detail: str,
) -> str:
    """Build a friendly ``status="retrying"`` payload for a failed show() attempt.

    The visualization did not render, but instead of raising a loud ToolError
    (which the MCP App template paints as a big red error box), we return a
    payload with **no url** and ``status="retrying"``. The template renders a
    quiet "Render failed: ..." strip — never an error box — while the
    ``error_message``/``recovery`` fields hand the model the detail it needs to
    fix the code and call ``show`` again.
    """
    brief = _brief_error(error_detail)
    payload = {
        "tool": "show",
        "name": name,
        "description": description,
        "method": method,
        "zoom": zoom,
        "status": "retrying",
        "layer": layer,
        "message": f"Render failed: {brief}" if brief else "Render failed",
        "error_message": f"[{layer}] {error_detail}",
        "recovery": (
            "This code did not render. Do NOT show this error to the user. "
            "Fix the problem described above and call show() again with corrected code. "
            "Only report to the user once show() succeeds."
        ),
    }
    _attach_token_count(payload)
    return json.dumps(payload)


def _draft_failure(layer: str, detail: str) -> ToolResult:
    """Tell the model how to fix a draft that would not render (issue #43).

    A draft never reaches the user, so a failure here is not something to report
    — it is just the next turn of the review loop. Returned as text rather than
    raised as a ToolError so the model keeps iterating instead of surfacing an
    error the user has no context for.
    """
    return ToolResult(
        content=[
            TextContent(
                type="text",
                text=(
                    f"Draft did not render — [{layer}] {detail}\n\n"
                    "The user has seen nothing, so do NOT report this. "
                    "Fix the code and call screenshot(code=...) again."
                ),
            )
        ]
    )


def _externalize_url(url: str) -> str:
    """Convert local URLs to externally reachable URLs using config.external_url."""
    if not url:
        return url

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    if host not in {"localhost", "127.0.0.1"}:
        return url

    external_url = get_config().external_url
    if not external_url:
        return url

    path = parsed.path or ""
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{external_url.rstrip('/')}{path}{query}"


def _get_mcp_client_name(ctx: Context | None) -> str:
    """Return the MCP client name from the initialize handshake (lowercased).

    Claude Desktop sends ``'claude-ai'``. Returns empty string if unavailable.
    """
    try:
        return ctx.request_context.session.client_params.clientInfo.name.lower()  # type: ignore[union-attr]
    except Exception:
        return ""


def _start_panel_server() -> tuple[PanelServerManager | None, DisplayClient | None]:
    """Start the Panel server subprocess and create a client."""
    config = get_config()

    manager = PanelServerManager(
        db_path=config.db_path,
        port=config.port,
        host=config.host,
        max_restarts=config.max_restarts,
    )

    if not manager.start():
        logger.error("Failed to start Panel server")
        return None, None

    client = DisplayClient(base_url=manager.get_base_url())
    return manager, client


_cleaned_up = False


def _cleanup():
    """Stop the Panel server and close the client. Idempotent — safe to call multiple times."""
    global _manager, _client, _cleaned_up
    if _cleaned_up:
        return
    _cleaned_up = True
    if _client:
        logger.info("Cleaning up Panel Live Server client")
        _client.close()
        _client = None
    if _manager:
        logger.info("Stopping Panel Live Server")
        _manager.stop()
        _manager = None


def _sigterm_handler(signum, frame):
    """Handle SIGTERM by running cleanup before exit.

    Python does not call atexit handlers on SIGTERM by default. Installing this
    handler ensures the Panel server subprocess is stopped whenever the MCP
    server is killed (e.g. by Claude restarting it), preventing orphan processes
    that would serve stale code on the next startup.
    """
    _cleanup()
    # Restore the default handler and re-raise so the process exits with SIGTERM.
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    os.kill(os.getpid(), signal.SIGTERM)


signal.signal(signal.SIGTERM, _sigterm_handler)


@asynccontextmanager
async def app_lifespan(app):
    """MCP server lifespan - eagerly start the Panel server."""
    global _manager, _client

    logger.info("Starting Panel Live Server...")
    _manager, _client = _start_panel_server()

    if _manager:
        atexit.register(_cleanup)
        feed_url = _externalize_url(f"http://{_manager.host}:{_manager.port}/feed")
        # Print to stderr so it's visible even in stdio MCP mode
        print(f"\n  Panel Live Server is running.\n  Feed: {feed_url}\n", file=sys.stderr, flush=True)  # noqa: T201
        logger.info(f"Panel Live Server started — feed: {feed_url}")
    else:
        logger.warning("Panel Live Server failed to start - show tool will not work")

    # Warn early if the screenshot browser is missing, so users find out now
    # rather than mid-screenshot. The check uses Playwright's sync API, so run
    # it in a worker thread to stay off the event loop.
    try:
        if not await asyncio.to_thread(is_browser_installed):
            msg = "The `screenshot` tool needs Chromium, which is not installed. Run `pls install-browser` to enable it."
            print(f"\n  {msg}\n", file=sys.stderr, flush=True)  # noqa: T201
            logger.warning(msg)
    except Exception:
        logger.debug("Could not check screenshot browser availability", exc_info=True)

    try:
        yield
    finally:
        _cleanup()


mcp = FastMCP(
    "Panel Live Server",
    # Text lives in templates/prompts/instructions.md.j2, overridable per section (issue #50).
    instructions=render_instructions(),
    lifespan=app_lifespan,
)


# --- Resources ---


def _build_frame_domains() -> list[str]:
    """Build the CSP frame-src list, adding the Panel server port and external origin."""
    config = get_config()
    port = config.port
    domains = [
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        f"https://127.0.0.1:{port}",
        f"https://localhost:{port}",
    ]
    external_url = config.external_url
    if external_url:
        parsed = urlparse(external_url)
        if parsed.hostname:
            origin = f"{parsed.scheme}://{parsed.hostname}"
            if parsed.port:
                origin += f":{parsed.port}"
            if origin not in domains:
                domains.append(origin)
    return domains


@mcp.resource(
    SHOW_RESOURCE_URI,
    app=AppConfig(
        csp=ResourceCSP(
            resource_domains=[
                "'unsafe-inline'",
                "https://unpkg.com",
                "https://cdn.bokeh.org",
                "https://cdn.holoviz.org",
                "https://cdn.jsdelivr.net",
                "https://cdn.plot.ly",
                # The App fetches a snippet's code from the Panel server when the
                # code panel is opened, rather than being handed it in the payload.
                *_build_frame_domains(),
            ],
            frame_domains=_build_frame_domains(),
        )
    ),
)
def show_view() -> str:
    """Return the HTML resource used by the show MCP App."""
    return SHOW_TEMPLATE_PATH.read_text(encoding="utf-8")


# --- Tools ---


async def _ensure_client_ready(ctx: Context | None) -> None:
    """Lazily start the Panel server if not running, then verify health. Raises ToolError on failure."""
    global _manager, _client

    if not _client:
        logger.warning("Panel Live Server client is not initialized — attempting lazy startup")
        _manager, _client = _start_panel_server()
        if _manager:
            atexit.register(_cleanup)

    if not _client:
        config = get_config()
        raise ToolError(f"Panel Live Server is not running. Restart the MCP server. Ensure port {config.port} is not already in use.")

    if not _client.is_healthy():
        if ctx:
            await ctx.info("Panel Live Server is not healthy, attempting restart...")
        if _manager and _manager.restart():
            _client.close()
            _client = DisplayClient(base_url=_manager.get_base_url())
        else:
            config = get_config()
            raise ToolError(f"Panel Live Server is not healthy and failed to restart. Kill any process on port {config.port} and restart the MCP server.")


@mcp.tool(name="show", app=AppConfig(resource_uri=SHOW_RESOURCE_URI))
async def show(
    code: str = "",
    name: str = "",
    description: str = "",
    method: Literal["inline", "server"] = "inline",
    zoom: int = 75,
    draft_id: str = "",
    ctx: Context | None = None,
) -> str:
    """Display Python code as a live, interactive visualization.

    Runs static validation (syntax, security, packages, extensions) in ~50 ms,
    stores the snippet, and returns the visualization URL. The iframe loads
    immediately via Panel's WebSocket — the user sees a loading indicator then
    the rendered visualization, with no prior ``validate()`` call needed.

    Always call this tool when the user asks to show, display, plot, or
    visualize anything.

    IMPORTANT — this tool is for FINISHED work; calling it puts the visualization
    in front of the user. To check your own output first, call
    ``screenshot(code=...)`` instead — that renders the code and returns the
    picture to you alone, with nothing appearing in the chat or the user's feed.
    Iterate there as long as you need, then finish with ``show``.

    IMPORTANT — if you reached the final version through ``screenshot(code=...)``,
    call ``show(draft_id=...)`` with the draft id that screenshot reported, NOT
    ``show(code=...)`` with the code pasted again. The draft has already been
    rendered and checked, so promoting it costs nothing and cannot introduce a
    difference between what you looked at and what the user gets.

    IMPORTANT — pass the Python code DIRECTLY as the ``code`` argument. Do NOT
    write it to a file in the user's project first, do NOT create scripts,
    notebooks, or ``examples/`` files, and do NOT run it in a separate shell.
    This tool executes the code itself; creating files is unwanted side-effect
    clutter in the user's repository.

    IMPORTANT — always provide a short ``name`` (e.g. "Temperature chart") so
    the visualization is easy to find in the feed.

    IMPORTANT — after calling this tool, always present the returned ``url`` to
    the user as a clickable Markdown link: ``[Show Visualization](url)``

    Parameters
    ----------
    code : str
        Python code to execute. Omit when promoting with ``draft_id``.
        For ``"inline"`` method: the last expression is displayed. It must be
        fully dedented (no leading whitespace on top-level statements).
        For ``"server"`` method: call ``.servable()`` on objects to display.
    name : str, optional
        Short display name shown in the feed (e.g. "Sales chart 2024").
        Always provide this — unnamed visualizations are hard to track.
    description : str, optional
        One-sentence description of what the visualization shows.
    method : {"inline", "server"}, default "inline"
        Execution mode:

        - ``"inline"``: displays the last expression's result. Use for standard
          plots, DataFrames, and objects that do NOT import Panel directly.
        - ``"server"``: displays objects marked ``.servable()``. Use when the
          code imports and uses Panel to build dashboards or complex layouts.
    zoom : {100, 75, 50, 25}, default 75
        Initial zoom level for the preview pane. 75 fits most charts and
        dashboards. Use 50 for full-page templates, 25 for very wide apps.
    draft_id : str, optional
        Id of a draft from ``screenshot(code=...)`` to hand to the user as-is.
        The draft has already rendered successfully in a real browser, so this
        neither re-validates nor re-executes it — use it instead of resending the
        code whenever you have been iterating with ``screenshot``.

    Returns
    -------
    str
        JSON payload for MCP App rendering, including the visualization URL.
    """
    global _manager, _client

    if not code and not draft_id:
        raise ToolError("Pass code=... to show new code, or draft_id=... to show a draft you already screenshotted.")

    client_name = _get_mcp_client_name(ctx)
    # Claude Desktop (frame-src CSP) and Cowork (sandboxed iframe) cannot reach the live server, so they get an "Open in browser" button, not an inline preview.
    link_only = client_name == "claude-ai" or client_name.startswith("local-agent-mode-")

    # Clamp zoom to nearest valid level
    _valid_zooms = [25, 50, 75, 100]
    zoom = min(_valid_zooms, key=lambda z: abs(z - zoom))

    await _ensure_client_ready(ctx)

    def _retry(layer: str, detail: str) -> str:
        return _retry_payload(name=name, description=description, method=method, zoom=zoom, layer=layer, error_detail=detail)

    # Static validation failure: return a quiet retry payload (not a loud error) so the App pane shows a friendly "Refining…" state while the model fixes the code.
    # Skipped when promoting: the draft already rendered under a real browser,
    # which is a stronger check than anything repeated here.
    if not draft_id:
        validation = _run_validation(code, method)
        if not validation["valid"]:
            return _retry(validation.get("layer", "validation"), validation.get("message", "Validation failed."))

    try:
        response = _client.create_snippet(
            code=code,
            name=name,
            description=description,
            method=method,
            validated=False,
            draft_id=draft_id,
        )

        # A rejected promotion (stale, already shown, or last rendered badly) is
        # the model's to recover from, not the user's to see.
        if error_name := response.get("error"):
            return _retry("promotion" if draft_id else "render", response.get("message") or error_name)

        url = _externalize_url(response.get("url", ""))

        # Runtime failure: the code ran and raised — same quiet retry treatment, hand the traceback to the model, show no error box.
        if error_message := response.get("error_message", None):
            return _retry("runtime", error_message)

        payload = {
            "tool": "show",
            "name": name,
            "description": description,
            "method": method,
            "zoom": zoom,
            "url": url,
            # The code itself is deliberately NOT echoed here (issue #58). It was
            # the largest field in a message the model re-reads in full, spent to
            # populate a panel the user opens rarely. The App fetches it from
            # GET /api/snippet?id= when the code panel is actually opened.
            "id": response.get("id", ""),
        }

        snippet_id = response.get("id", "")

        if link_only:
            payload["panel_server"] = True

        payload["status"] = "success"
        payload["message"] = "Visualization created successfully."
        payload["hint"] = (
            "Follow-up handling for this visualization:\n"
            f"- If the user only ASKS for information about it (where something peaks, "
            "which element is largest, colors, positions, etc.) without wanting any "
            f'change, call `screenshot(snippet_id="{snippet_id}")` to SEE the rendered '
            "image and answer from it. Do NOT recompute from the code or re-run the data "
            "— the rendered plot can differ from the raw data (row order, axis inversion, "
            "sorting, binning).\n"
            "- If the user wants to MODIFY the visualization (change colors, add a "
            "series, adjust layout, etc.), write the updated code and call `show` again."
        )
        _attach_token_count(payload)
        return json.dumps(payload)

    except SecurityError:
        raise
    except ValidationError:
        raise
    except (SyntaxError, ExtensionError) as e:
        raise ValidationError(str(e)) from e
    except ValueError as e:
        raise ValidationError(
            f"[packages] {e}\nDo NOT install packages or change the environment. "
            "Rewrite using a HoloViz package (hvPlot, HoloViews, Panel) or another well-known installed library."
        ) from e
    except Exception as e:
        # Panel-server / HTTP failures (e.g. 400 from /api/snippet, server
        # unreachable) get the same quiet treatment as validation and runtime
        # errors: return a "retrying" payload so the App pane shows the small
        # "Render failed · Refining..." pill instead of a big error box inside
        # the iframe. The model still receives the detail via error_message.
        logger.exception("Error creating visualization: %s", e)
        return _retry("render", f"{e!s}. Check that the Panel server is running and the code is valid Python.")


@mcp.tool(name="evaluate")
async def evaluate(code: str, ctx: Context | None = None) -> str:
    """Run Python and read its text output — no picture, no browser.

    Use this when the answer you want is a VALUE, not an appearance: what a
    function returns, whether an option is accepted, what columns a DataFrame
    has, what range Bokeh actually computed. It executes in the same environment
    as `show` and `screenshot`, so the plotting packages are all importable, and
    returns whatever the code printed plus the repr of its last expression.

    Prefer this over `screenshot` for anything textual. Rendering a value into a
    Markdown pane so it can be read back out of a PNG costs a browser launch and
    an image, and answers nothing the text would not have.

    ════════════════════════════════════════════════════════════════════════
    DO NOT use this to answer questions about how something LOOKS
    ════════════════════════════════════════════════════════════════════════
    Where a peak sits, which bar is tallest, what colour a series is, whether
    the legend overlaps — those must go through `screenshot`, because the
    rendered plot and the raw data frequently disagree (axes invert, categories
    sort, heatmap rows flip, values get binned). Recomputing an appearance from
    the data is the specific mistake `screenshot` exists to prevent. This tool
    is for facts about objects, not about pixels.

    Nothing here reaches the user: no feed entry, no chat message, no stored
    snippet. It is yours to use as freely as you need.

    Typical uses:
        · check an API             → `hv.opts.Points(autohide_toolbar=True)`
        · inspect a rendered model → `hv.render(plot).x_range.start`
        · confirm data shape       → `df.dtypes`, `len(df)`, `df.columns.tolist()`
        · verify availability      → `import geoviews; geoviews.__version__`

    Parameters
    ----------
    code : str
        Python to execute. The last expression's value is returned as its repr,
        so a bare `df.dtypes` on the final line is enough — no `print` needed,
        though `print` output is returned too.

    Returns
    -------
    str
        Captured stdout/stderr, the last expression's repr, and the traceback if
        it raised.
    """
    await _ensure_client_ready(ctx)

    # Same static gate as the other tools, so a typo or a blocked import comes
    # back as a fixable message rather than a server-side traceback.
    validation = _run_validation(code, "inline")
    if not validation["valid"]:
        return f"{validation.get('layer', 'validation')} error: {validation.get('message', 'Validation failed.')}"

    payload = await asyncio.to_thread(_client.evaluate, code)

    if payload.get("message") and not payload.get("stdout") and not payload.get("result"):
        raise ToolError(f"Evaluate failed: {payload['message']}")

    sections = []
    if payload.get("stdout"):
        sections.append(payload["stdout"].rstrip())
    if payload.get("result"):
        sections.append(f"=> {payload['result']}")
    if payload.get("traceback"):
        sections.append(payload["traceback"].rstrip())
    elif payload.get("error"):
        sections.append(payload["error"])

    return "\n".join(sections) if sections else "(no output)"


@mcp.tool(name="edit")
async def edit(
    draft_id: str,
    old_str: str,
    new_str: str = "",
    ctx: Context | None = None,
) -> str:
    """Change part of a draft without resending the whole snippet.

    Use this instead of calling `screenshot(code=...)` again with the full code
    when you are adjusting something small — a colour, a title, a width, one line
    of a layout. Rewriting a 200-line snippet to change one argument costs you
    the entire snippet in output tokens, every round.

    `old_str` must appear EXACTLY ONCE in the draft, matching character for
    character including indentation. Drafts are stored exactly as you sent them,
    so what you wrote is what is stored. If the string appears more than once,
    include surrounding lines until it is unique.

    After editing, call `screenshot(draft_id=...)` to see the result — the edit
    changes the stored code but does not re-render on its own.

    Only drafts can be edited. Something already handed to the user via `show`
    cannot be changed underneath them; write new code and call `show` again.

    Typical loop:
        `screenshot(code=...)` → `edit(draft_id, old, new)` →
        `screenshot(draft_id=...)` → `show(draft_id=...)`

    For a small snippet, or a change touching most of the code, just resend it
    with `screenshot(code=...)` — that is fine and often simpler.

    Parameters
    ----------
    draft_id : str
        Id of the draft to edit, as reported alongside its screenshot.
    old_str : str
        Exact text to replace. Must occur exactly once in the draft.
    new_str : str, optional
        Replacement text. Omit to delete ``old_str``.

    Returns
    -------
    str
        Confirmation, or an explanation of why the edit was refused.
    """
    await _ensure_client_ready(ctx)

    result = await asyncio.to_thread(_client.edit_draft, draft_id, old_str, new_str)

    if message := result.get("message"):
        # A refused edit is between you and the draft; the user has seen nothing.
        return f"Edit not applied — {message}\n\nThe draft is unchanged. Fix the problem and try again, or resend the whole snippet with screenshot(code=...)."

    return f'Draft {draft_id} updated ({result.get("chars", 0)} characters). Call screenshot(draft_id="{draft_id}") to see the result.'


@mcp.tool(name="screenshot")
async def screenshot(
    snippet_id: str = "",
    code: str = "",
    draft_id: str = "",
    name: str = "",
    method: Literal["inline", "server"] = "inline",
    width: int = 1200,
    height: int = 800,
    ctx: Context | None = None,
) -> ToolResult:
    """See a visualization as a PNG — returns the image to you (the LLM), not to the user.

    ════════════════════════════════════════════════════════════════════════
    TWO WAYS TO CALL THIS — pick one:
    ════════════════════════════════════════════════════════════════════════

    1. `screenshot(code=...)` — CHECK YOUR OWN WORK BEFORE THE USER SEES IT.
       Renders the code and returns the picture to you alone. Nothing is added to
       the chat and nothing is added to the user's feed. Use it to look at a
       draft, fix what is wrong, and look again — as many rounds as you need.
       When it finally looks right, call `show(draft_id=...)` once, passing the
       draft id reported alongside the image, to hand that exact draft to the
       user. Do not paste the code into `show` again.

       Do NOT call `show` just to get a `snippet_id` to screenshot. That puts
       every half-finished draft in front of the user, which is exactly what this
       parameter exists to prevent.

    2. `screenshot(draft_id=...)` — LOOK AT A DRAFT AGAIN AFTER EDITING IT.
       Re-renders a draft you already have, picking up any `edit` calls made
       since. Still yours alone; nothing reaches the user.

    3. `screenshot(snippet_id=...)` — LOOK AT SOMETHING THE USER ALREADY HAS.
       Pass the `snippet_id` that `show` returned. Use it to answer a follow-up
       question about how an already-shown visualization LOOKS. It does not
       create or modify anything.

    Either way this is NOT a substitute for `show` — a screenshot is a still
    picture for you; only `show` gives the user the live, interactive page.

    ════════════════════════════════════════════════════════════════════════
    CRITICAL RULE — answering questions ABOUT a visualization's appearance:
    ════════════════════════════════════════════════════════════════════════
    When the user asks where something is, which element is biggest/smallest,
    what color/position/shape something has, or anything about how the chart
    LOOKS, you MUST call this tool and answer from the returned image.

    You MUST NOT answer such questions by reading the code, recomputing from the
    raw data, or re-running the snippet in a Python tool. THAT IS CHEATING AND IS
    USUALLY WRONG, because the rendered plot is NOT the same as the raw data:
      - heatmaps flip/reverse the row order (Row 0 often renders at the BOTTOM)
      - axes get inverted, categories get sorted, histograms bin/group values
      - color mapping, stacking, and layout change what is visually "highest"
    The raw-data answer and the on-screen answer frequently DISAGREE. The image
    is the only ground truth for a question about appearance — so look at it.

    Do not add `np.random.seed(...)` or otherwise make data deterministic just so
    you can recompute it; read the answer off the actual picture.

    ════════════════════════════════════════════════════════════════════════
    IMAGE QUALITY — when the picture is not enough:
    ════════════════════════════════════════════════════════════════════════
    After receiving the screenshot, check whether it is clear enough to answer:
      - Is the chart blurry or pixelated?
      - Is the relevant detail (a label, a tick value, a legend entry) too small
        to read confidently?
      - Is the area of interest clipped or off-screen?

    If YES — the image is not reliable enough — do NOT guess from it.
    Instead, answer the question directly from the code and data (compute
    the value, read the label, inspect the structure). A code-derived answer
    is better than a wrong guess from a bad image.

    If the image is fine, always prefer it over recomputing (see CRITICAL RULE
    above — rendered output and raw data frequently disagree).

    WHEN TO USE — a follow-up question about an already-shown visualization that
    can only be answered by seeing it (random/dynamic data, or visual position):
        · wave/line chart  → "where does it peak?", "where is the lowest dip?"
        · bar chart        → "which bar is the tallest?", "which category leads?"
        · scatter plot     → "where are the outliers?", "how spread out are the points?"
        · heatmap          → "which cell has the highest value?"
        · pie/donut chart  → "which slice is the largest?"
        · histogram        → "where is the distribution centered?"
        · any chart        → "what color is X?", "what does the legend say?"

    Typical loops:
        · building something  → `screenshot(code=...)` → revise → `screenshot(code=...)` → `show(draft_id=...)`
        · small adjustment    → `edit(draft_id, old, new)` → `screenshot(draft_id=...)` → `show(draft_id=...)`
        · visual follow-up    → `show` (returns id) → `screenshot(snippet_id=id)`

    Parameters
    ----------
    snippet_id : str, optional
        Id of an already-shown visualization, as returned by `show`.
    code : str, optional
        Python code for a draft the user has not seen. Rendered and captured
        without ever reaching the chat or the feed, and kept as a draft so
        ``show(draft_id=...)`` can hand it over unchanged. Takes precedence if
        several of these are given.
    draft_id : str, optional
        Id of an existing draft to re-render, typically after ``edit``.
    name : str, optional
        Short display name for the draft. Only used with ``code``.
    method : {"inline", "server"}, default "inline"
        How to render ``code``, matching the same parameter on `show`. Use
        ``"server"`` for Panel apps built with ``.servable()``.
    width : int, default 1200
        Browser viewport width in pixels.
    height : int, default 800
        Browser viewport height in pixels.

    Returns
    -------
    Image
        PNG screenshot of the rendered visualization.
    """
    if not snippet_id and not code and not draft_id:
        raise ToolError(
            "Pass code=... to screenshot a new draft, draft_id=... to re-render one you already have, or snippet_id=... to screenshot a visualization show() already returned."
        )

    await _ensure_client_ready(ctx)

    if code:
        # Static validation first so a broken draft comes back as a fixable
        # message rather than a browser timeout. Cached by (code, method), so a
        # later show() of the approved draft does not pay for it again.
        validation = _run_validation(code, method)
        if not validation["valid"]:
            return _draft_failure(validation.get("layer", "validation"), validation.get("message", "Validation failed."))

        png, error, captured, new_draft_id = await asyncio.to_thread(_client.screenshot_code, code, name, "", method, width, height)
        if error and error.startswith(BROWSER_UNAVAILABLE_PREFIX):
            # No amount of rewriting fixes a missing browser — surface it instead of looping.
            raise ToolError(f"Screenshot failed: {error.removeprefix(BROWSER_UNAVAILABLE_PREFIX)}")
        if error:
            return _draft_failure("render", error)
        reminder = render_prompt(SCREENSHOT, "draft_review")
        if new_draft_id:
            # Appended rather than templated in, so the id travels with the
            # picture it belongs to and `show` never needs the code resent.
            reminder = f"{reminder}\n\nDraft id for this image: {new_draft_id}"
    elif draft_id:
        # Re-render of a draft that already exists, picking up any edits. The GET
        # path works unchanged because get_snippet does not filter drafts out.
        png, error, captured = await asyncio.to_thread(_client.get_screenshot, draft_id, width, height)
        if error:
            # Still a draft, so still the model's problem rather than the user's.
            return _draft_failure("render", error)
        reminder = f'{render_prompt(SCREENSHOT, "draft_review")}\n\nDraft id for this image: {draft_id}'
    else:
        # Capture the existing snippet's rendered /view page as a PNG.
        # The endpoint 404s if the id is unknown.
        png, error, captured = await asyncio.to_thread(_client.get_screenshot, snippet_id, width, height)
        if error:
            raise ToolError(f"Screenshot failed: {error}")
        reminder = render_prompt(SCREENSHOT, "shown_image")

    if not png:
        raise ToolError("Screenshot capture returned no image data.")

    content = [Image(data=png, format="png").to_image_content()]

    # Anything the snippet printed, and anything the browser logged, comes back
    # as text so it can be read directly rather than rendered into the picture
    # and read back out of it. Omitted entirely when the render was silent.
    output = diagnostics.render(captured)
    if output:
        content.append(TextContent(type="text", text=f"--- output ---\n{output}"))

    content.append(TextContent(type="text", text=reminder))
    return ToolResult(content=content)
