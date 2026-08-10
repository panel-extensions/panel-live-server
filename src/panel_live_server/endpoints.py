"""REST API endpoints for the Display System.

This module implements Tornado RequestHandler classes that provide
HTTP endpoints for creating visualizations and checking server health.
"""

import contextlib
import io
import json
import logging
import sys
import traceback
from datetime import datetime
from datetime import timezone

from tornado.web import RequestHandler

from panel_live_server import diagnostics
from panel_live_server import screenshot
from panel_live_server import usage
from panel_live_server.config import get_config
from panel_live_server.database import get_db
from panel_live_server.utils import execute_in_module
from panel_live_server.utils import extract_last_expression
from panel_live_server.utils import validate_code
from panel_live_server.validation import SecurityError
from panel_live_server.validation import ast_check
from panel_live_server.validation import ruff_format

logger = logging.getLogger(__name__)


def _get_external_base_url(request_host: str) -> str | None:
    """Get external base URL for links returned to clients.

    Returns ``config.external_url`` when set (auto-detected from environment),
    otherwise ``None`` (caller should fall back to the request URL).
    """
    try:
        return get_config().external_url or None
    except Exception:
        return None


class SnippetEndpoint(RequestHandler):
    """Tornado RequestHandler for /api/snippet endpoint."""

    def set_default_headers(self):
        """Allow the MCP App to read a snippet's code from its own origin.

        ``show.html`` runs inside the host application, not on this server, so a
        cross-origin read needs this header. The data is already reachable by
        anything that can reach this port, which is the same machine, so the
        header grants nothing that was not already available.
        """
        self.set_header("Access-Control-Allow-Origin", "*")

    def get(self):
        """Return a stored snippet's code and metadata for ``?id=``.

        Exists so the ``show`` payload no longer has to carry the code itself.
        That echo was paid on every call, in the model's context, to populate a
        panel the user opens rarely — so the code is fetched here instead, when
        it is actually looked at.

        The code is formatted on the way out rather than on the way in. Storage
        stays byte-identical to what the author sent, so ``old_str`` edits keep
        matching, while a human opening the panel still sees tidy code. This runs
        only when the panel is actually opened, so the cost is paid by the click.
        """
        snippet_id = self.get_argument("id", "")
        if not snippet_id:
            self.set_status(400)
            self.set_header("Content-Type", "application/json")
            self.write({"error": "ValueError", "message": "Missing 'id' parameter"})
            return

        snippet = get_db().get_snippet(snippet_id)
        if snippet is None:
            self.set_status(404)
            self.set_header("Content-Type", "application/json")
            self.write({"error": "NotFound", "message": f"Snippet {snippet_id} not found"})
            return

        self.set_status(200)
        self.set_header("Content-Type", "application/json")
        self.write(
            {
                "id": snippet.id,
                "code": ruff_format(snippet.app),
                "name": snippet.name,
                "description": snippet.description,
                "method": snippet.method,
                "status": snippet.status,
            }
        )

    def post(self):
        """Handle POST requests to store snippets and create visualizations."""
        # Get database instance
        db = get_db()

        try:
            # Parse JSON body
            request_body = json.loads(self.request.body.decode("utf-8"))

            # Extract parameters
            code = request_body.get("code", "")
            name = request_body.get("name", "")
            description = request_body.get("description", "")
            method = request_body.get("method", "inline")
            validated = request_body.get("validated", False)
            draft_id = request_body.get("draft_id", "")

            if draft_id:
                # Counted with zero characters on purpose: a promotion hands a
                # finished visualization to the user without resending its code,
                # and that saving is only visible if the call is counted at all.
                usage.record("promote", 0)
                # Promotion. The draft has already rendered successfully under
                # Playwright, so there is nothing left to check and nothing to
                # run: hand the existing row to the user rather than storing and
                # executing the same code a second time.
                snippet = db.promote_draft(draft_id, name=name or None, description=description or None)
            else:
                usage.record("show", len(code))
                # `validated` says the caller already ran the static layers itself.
                # Kept as one flag on the wire; the three-way split is server-side.
                #
                # format=False regardless: an agent may edit this snippet later by
                # string match, and reformatting on the way in is what makes that
                # miss. Formatting is applied when the code is read for display.
                snippet = db.create_visualization(
                    app=code,
                    name=name,
                    description=description,
                    method=method,
                    run_static=not validated,
                    format=False,
                    execute=not validated,
                )

            if base_url := _get_external_base_url(self.request.host):
                url = f"{base_url}/view?id={snippet.id}"
            else:
                full_url = self.request.full_url()
                url = full_url.replace("/api/snippet", "/view?id=" + snippet.id)

            result = {
                "id": snippet.id,
                "url": url,
            }
            if snippet.error_message:
                result["error_message"] = snippet.error_message

            # Return success response
            self.set_status(200)
            self.set_header("Content-Type", "application/json")
            self.write(result)

        except SyntaxError as e:
            self.set_status(400)
            self.set_header("Content-Type", "application/json")
            self.write({"error": "SyntaxError", "message": str(e)})
        except SecurityError as e:
            self.set_status(400)
            self.set_header("Content-Type", "application/json")
            self.write({"error": "SecurityError", "message": str(e)})
        except ValueError as e:
            self.set_status(400)
            self.set_header("Content-Type", "application/json")
            self.write({"error": "ValueError", "message": str(e)})
        except Exception as e:
            # Handle all other errors
            logger.exception("Error in /api/snippet endpoint")
            self.set_status(500)
            self.set_header("Content-Type", "application/json")
            self.write(
                {
                    "error": "InternalError",
                    "message": str(e),
                    "traceback": traceback.format_exc(),
                }
            )


def _local_host(host: str) -> str:
    """Return a host usable for self-connections (the server screenshots itself)."""
    return "127.0.0.1" if host in ("0.0.0.0", "::", "") else host


class ScreenshotEndpoint(RequestHandler):
    """Render a snippet's ``/view`` page to a PNG.

    Loads the live ``/view`` page in a headless browser (Playwright) and returns
    a PNG, giving LLMs a picture of the *rendered* output — layout, fonts, and
    margins as a user would see them. When no browser is installed/launchable
    this returns HTTP 503 with an install hint so the caller can surface a clear
    message instead of failing opaquely.

    ``GET /api/screenshot?id=...`` captures a snippet that already exists.

    ``POST /api/screenshot`` with a JSON body of ``{"code": ..., "name": ...,
    "description": ..., "method": ...}`` captures code that has never been shown
    (issue #43). The row is stored as a *draft*: kept out of the feed and out of
    search, so an agent can iterate without anything reaching the user. The id is
    returned in the ``X-PLS-Draft-Id`` header, and ``show(draft_id=...)`` later
    promotes the draft the agent settles on — which is what lets the final show
    cost no execution at all. Drafts are swept by age, not on the way out.

    The draft is deliberately *not* executed before the capture. Loading ``/view``
    runs it and stamps the row with a status and, on failure, a traceback — so the
    row is re-read afterwards and a failed draft comes back as its traceback
    rather than as a picture of one. One execution per draft, not two.

    Query parameters (GET) / body fields (POST)
    -------------------------------------------
    id : str
        Snippet id to render (GET only, required).
    width, height : int
        Viewport size in px (default from config).
    full_page : bool
        Capture the full scrollable page rather than just the viewport.
    """

    def _error(self, status: int, message: str, error: str | None = None) -> None:
        """Write a JSON error response with *status*."""
        self.set_status(status)
        self.set_header("Content-Type", "application/json")
        self.write({"error": error or message, "message": message})

    async def get(self):
        """Capture and return the snippet identified by ``?id=`` as a PNG."""
        snippet_id = self.get_argument("id", "")
        if not snippet_id:
            self._error(400, "Missing 'id' parameter")
            return

        if not get_db().get_snippet(snippet_id):
            self._error(404, f"Snippet {snippet_id} not found")
            return

        if captured := await self._capture(
            snippet_id,
            width=self.get_argument("width", ""),
            height=self.get_argument("height", ""),
            full_page=self.get_argument("full_page", "false"),
        ):
            self._write_png(*captured)

    async def post(self):
        """Store the posted code as a draft, capture it, and hand back the picture."""
        try:
            body = json.loads(self.request.body.decode("utf-8"))
        except ValueError:
            self._error(400, "Request body must be JSON")
            return

        code = body.get("code", "")
        if not code:
            self._error(400, "Missing 'code' in request body")
            return

        usage.record("screenshot", len(code))

        db = get_db()

        # Sweep here rather than at startup: drafts accumulate fastest during a
        # long iterating session, which is exactly when a startup-only sweep has
        # already run and will not run again. An indexed delete on the way in is
        # cheap and keeps the bound tied to activity.
        try:
            db.delete_stale_drafts(get_config().draft_retention_hours)
        except Exception:
            logger.warning("Could not sweep stale drafts", exc_info=True)

        try:
            # execute=False: the Playwright render below is itself the error
            # detector — /view runs the code and writes status + traceback to the
            # row — so executing here as well would run every draft twice for one
            # picture, which is the cost this path exists to avoid.
            # format=False: store the draft verbatim so the text on disk matches
            # the text the model holds, and formatting is deferred to promotion.
            snippet = db.create_visualization(
                app=code,
                name=body.get("name", ""),
                description=body.get("description", ""),
                method=body.get("method", "inline"),
                format=False,
                execute=False,
                draft=True,
            )
        except SecurityError as e:
            self._error(400, str(e), error="SecurityError")
            return
        except SyntaxError as e:
            self._error(400, str(e), error="SyntaxError")
            return
        except Exception as e:
            self._error(400, str(e), error=type(e).__name__)
            return

        captured = await self._capture(
            snippet.id,
            width=str(body.get("width", "")),
            height=str(body.get("height", "")),
            full_page=str(body.get("full_page", False)),
        )
        if captured is None:
            # Nothing usable came back, so the row is of no further interest.
            db.delete_snippet(snippet.id)
            return

        # Read the verdict the render just wrote instead of having executed the
        # code a second time up front to find out. A snippet that raised renders
        # as an error pane, so the PNG would otherwise be a picture of a traceback
        # rather than the traceback itself.
        rendered = db.get_snippet(snippet.id)
        if rendered is not None and rendered.status == "error":
            db.delete_snippet(snippet.id)
            self._error(400, rendered.error_message or "The draft failed to render.", error="RuntimeError")
            return

        # Kept, not deleted: show(draft_id=...) promotes this row without
        # re-running it, which is what removes the final redundant execution.
        self.set_header(diagnostics.DRAFT_ID_HEADER, snippet.id)
        self._write_png(*captured)

    async def _capture(self, snippet_id: str, *, width: str, height: str, full_page: str) -> tuple[bytes, str] | None:
        """Screenshot ``/view?id=<snippet_id>``, returning the PNG and its diagnostics.

        Returns ``(png, diagnostics_header)`` on success, or ``None`` when the
        capture failed — in which case an error response has already been written
        and the caller should return immediately.

        The response is deliberately not written here: the POST path has to inspect
        the row this render just stamped before it can decide whether the caller
        gets a picture or a traceback.
        """
        config = get_config()
        try:
            viewport_width = int(width or config.screenshot_width)
            viewport_height = int(height or config.screenshot_height)
        except ValueError:
            self._error(400, "width and height must be integers")
            return None

        view_url = f"http://{_local_host(config.host)}:{config.port}/view?id={snippet_id}"

        console_lines: list[str] = []
        try:
            png = await screenshot.capture_png(
                view_url,
                width=viewport_width,
                height=viewport_height,
                full_page=full_page.lower() in ("1", "true", "yes"),
                settle_ms=config.screenshot_settle_ms,
                timeout_ms=config.screenshot_timeout_ms,
                console_sink=console_lines,
            )
        except screenshot.PlaywrightUnavailableError as e:
            self.set_status(503)
            self.set_header("Content-Type", "application/json")
            self.write({"error": "PlaywrightUnavailable", "message": str(e)})
            return None
        except Exception as e:
            logger.exception(f"Error capturing screenshot for snippet {snippet_id}")
            self.set_status(500)
            self.set_header("Content-Type", "application/json")
            self.write({"error": str(e), "traceback": traceback.format_exc()})
            return None

        # Hand back what the render produced as well as how it looked. Carried in
        # a header so the body stays a plain image/png for existing consumers.
        payload = diagnostics.build(diagnostics.pop(snippet_id), console_lines)
        return png, diagnostics.encode(payload) if payload else ""

    def _write_png(self, png: bytes, diagnostics_header: str) -> None:
        """Write *png* as the response body, carrying diagnostics in a header."""
        self.set_status(200)
        self.set_header("Content-Type", "image/png")
        if diagnostics_header:
            self.set_header(diagnostics.HEADER, diagnostics_header)
        self.write(png)


class SnippetEditEndpoint(RequestHandler):
    """Change part of a stored snippet without resending the whole thing.

    ``POST /api/snippet/edit`` with ``{"snippet_id": ..., "old_str": ..., "new_str":
    ...}`` replaces one occurrence of ``old_str`` in the snippet's code.

    A draft loop otherwise costs a full rewrite per turn: the model resends every
    line to change a colour. Substring editing makes the output proportional to
    the change rather than to the snippet.

    This works only because drafts are stored verbatim (``format=False``): if the
    server reformatted on the way in, the text the model is matching against
    would not be the text on disk, and ``old_str`` would miss for reasons no one
    could see.

    A draft is edited in place. Something the user has already been shown is
    *forked* instead: the edit lands on a new draft and the live row is left
    alone, so nothing changes under someone who is looking at it. Refusing these
    outright was the earlier design, and it cost a wasted call on the commonest
    shape there is — show, then "tweak that".
    """

    def _error(self, status: int, message: str, error: str | None = None) -> None:
        """Write a JSON error response with *status*."""
        self.set_status(status)
        self.set_header("Content-Type", "application/json")
        self.write({"error": error or message, "message": message})

    def post(self):
        """Apply a single substring replacement to a stored snippet."""
        try:
            body = json.loads(self.request.body.decode("utf-8"))
        except ValueError:
            self._error(400, "Request body must be JSON")
            return

        snippet_id = body.get("snippet_id", "")
        old_str = body.get("old_str", "")
        new_str = body.get("new_str", "")

        if not snippet_id:
            self._error(400, "Missing 'snippet_id' in request body")
            return
        if not old_str:
            self._error(400, "Missing 'old_str' in request body")
            return

        # The saving this endpoint exists for is the gap between this number and
        # what a full resend through /api/screenshot would have cost.
        usage.record("edit", len(old_str) + len(new_str))

        db = get_db()
        snippet = db.get_snippet(snippet_id)
        if snippet is None:
            self._error(404, f"No snippet found with id {snippet_id!r}. Drafts are cleared after a while — screenshot the code again to get a fresh one.")
            return
        occurrences = snippet.app.count(old_str)
        if occurrences == 0:
            self._error(
                400,
                "old_str was not found in the code. It must match the stored text exactly, including indentation. "
                "Screenshot it again if you have lost track of its current contents.",
                error="NoMatch",
            )
            return
        if occurrences > 1:
            self._error(
                400,
                f"old_str appears {occurrences} times, so the edit is ambiguous. Include more surrounding context to make it unique.",
                error="AmbiguousMatch",
            )
            return

        edited = snippet.app.replace(old_str, new_str, 1)

        # Cheap syntax gate. The alternative is finding out by launching a browser,
        # which is the expensive way to learn about a missing bracket. Nothing is
        # written so a rejected edit cannot leave anything in a worse state than the
        # model last saw.
        if syntax_error := ast_check(edited):
            self._error(400, f"That edit would leave the code unparsable: {syntax_error}", error="SyntaxError")
            return

        # Run the edited code here so the result can go straight to `show`.
        # promote_draft gates on status == "success", so without this an edit had to
        # be screenshotted purely to stamp the row — a Playwright launch to change a
        # colour. It also stops a stale status from the pre-edit render waving
        # through code that no longer runs.
        if run_error := validate_code(edited):
            self._error(400, f"The edited code no longer runs: {run_error}", error="RuntimeError")
            return

        if snippet.draft:
            db.update_snippet(snippet_id, app=edited, status="success")
            result_id, forked = snippet_id, False
        else:
            # Already shown, so it must not change under the user. Fork instead:
            # the edit lands on a new draft and the live snippet stays put until the
            # model shows the fork.
            fork = db.create_visualization(
                app=edited,
                name=snippet.name,
                description=snippet.description,
                method=snippet.method,
                # Verbatim so the next old_str still matches. execute=False because
                # validate_code above already ran it; running twice for one edit is
                # the cost this endpoint exists to avoid.
                format=False,
                execute=False,
                draft=True,
            )
            db.update_snippet(fork.id, status="success")
            result_id, forked = fork.id, True

        self.set_status(200)
        self.set_header("Content-Type", "application/json")
        # Deliberately no code echo: sending the whole snippet back would undo the
        # saving the edit just made.
        self.write({"id": result_id, "chars": len(edited), "forked": forked})


class EvaluateEndpoint(RequestHandler):
    """Run code and return its text output — no rendering, no browser, no feed.

    ``POST /api/evaluate`` with ``{"code": ...}`` executes the code and returns
    ``{"stdout": ..., "result": ..., "error": ..., "traceback": ...}``.

    This exists because the answer an agent wants is often a *value*, not a
    picture: does this option exist, what does this return, what are the columns,
    what range did Bokeh actually compute. Routing those through ``/screenshot``
    means launching Chromium and rendering the text into an image purely so it
    can be read back out of one. This is the same environment — the packages that
    make the display server useful — reached without the browser.

    Execution happens here, in the display-server process, exactly as ``/view``
    does. The MCP process never execs snippet code.

    Nothing is written to the database, so an evaluation cannot reach the feed.
    """

    def post(self):
        """Execute the posted code and return its output as JSON."""
        try:
            body = json.loads(self.request.body.decode("utf-8"))
        except ValueError:
            self.set_status(400)
            self.set_header("Content-Type", "application/json")
            self.write({"error": "ValueError", "message": "Request body must be JSON"})
            return

        code = body.get("code", "")
        if not code:
            self.set_status(400)
            self.set_header("Content-Type", "application/json")
            self.write({"error": "ValueError", "message": "Missing 'code' in request body"})
            return

        usage.record("evaluate", len(code))

        # Belt and braces: the MCP tool validates before calling, but this
        # endpoint is reachable on its own.
        if syntax_error := ast_check(code):
            self.set_status(400)
            self.set_header("Content-Type", "application/json")
            self.write({"error": "SyntaxError", "message": syntax_error})
            return

        buffer = io.StringIO()
        result_repr = ""
        error = ""
        tb = ""
        module_name = f"pls_eval_{abs(hash(code)) % (10**10)}"

        try:
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                statements, last_expr = extract_last_expression(code)
                # The finally must cover execute_in_module itself, not just the
                # eval below it. It is called with cleanup=False so the namespace
                # survives for that eval, which means it does NOT unregister the
                # module when the statements raise — so code ending in `raise`
                # would otherwise leave a pls_eval_* module in sys.modules for
                # the life of the process.
                try:
                    namespace = execute_in_module(statements, module_name=module_name, cleanup=False)
                    if last_expr:
                        value = eval(last_expr, namespace)  # noqa: S307 - same trust boundary as /view
                        # None is what a statement-like trailing call returns;
                        # reporting it as a result would be noise.
                        if value is not None:
                            result_repr = repr(value)
                finally:
                    sys.modules.pop(module_name, None)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            tb = traceback.format_exc()

        self.set_status(200)
        self.set_header("Content-Type", "application/json")
        self.write(
            {
                "stdout": diagnostics.truncate(buffer.getvalue()),
                "result": diagnostics.truncate(result_repr),
                "error": error,
                "traceback": diagnostics.truncate(tb),
            }
        )


class HealthEndpoint(RequestHandler):
    """Tornado RequestHandler for /api/health endpoint."""

    def get(self):
        """Handle GET requests to check server health.

        The payload reports the interpreter running this server (``sys.prefix``
        and ``sys.executable``) so a manager can tell whether a server already
        listening on the port belongs to its own environment before adopting it.

        It also carries the session's usage counters (issue #58), which is how
        the cost of a working session gets measured rather than estimated. They
        live here rather than in the ``show`` payload because this is a plain GET
        nobody pays context for.
        """
        self.set_status(200)
        self.set_header("Content-Type", "application/json")
        self.write(
            {
                "status": "healthy",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "prefix": sys.prefix,
                "executable": sys.executable,
                "usage": usage.snapshot(),
            }
        )
