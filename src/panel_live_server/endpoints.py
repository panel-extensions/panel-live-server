"""REST API endpoints for the Display System.

This module implements Tornado RequestHandler classes that provide
HTTP endpoints for creating visualizations and checking server health.
"""

import json
import logging
import traceback
from datetime import datetime
from datetime import timezone

from tornado.web import RequestHandler

from panel_live_server.config import get_config
from panel_live_server.database import get_db
from panel_live_server.validation import SecurityError

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
            method = request_body.get("method", "jupyter")

            # Call shared business logic
            snippet = db.create_visualization(
                app=code,
                name=name,
                description=description,
                method=method,
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


class EmbedEndpoint(RequestHandler):
    """Render a snippet to static embeddable HTML.

    Returns a self-contained HTML page (using CDN resources for JS/CSS)
    suitable for direct DOM rendering inside the MCP App.  This avoids
    nested iframes which Claude Desktop does not allow.

    Both ``jupyter`` and ``panel`` methods are supported:

    - **jupyter**: executes code, evaluates the last expression, wraps
      with ``pn.panel()`` and exports via ``save(resources='cdn')``.
    - **panel**: executes code while capturing any objects that call
      ``.servable()``, then exports a static snapshot.  Python callbacks
      and server-side interactivity are lost, but the visual output is
      preserved.
    """

    def get(self):
        """Handle GET requests to render a snippet as static HTML."""
        import io
        import sys

        import panel as pn

        from panel_live_server.database import get_db
        from panel_live_server.utils import execute_in_module
        from panel_live_server.utils import extract_last_expression
        from panel_live_server.utils import find_extensions

        snippet_id = self.get_argument("id", "")
        if not snippet_id:
            self.set_status(400)
            self.set_header("Content-Type", "application/json")
            self.write({"error": "Missing 'id' parameter"})
            return

        db = get_db()
        snippet = db.get_snippet(snippet_id)
        if not snippet:
            self.set_status(404)
            self.set_header("Content-Type", "application/json")
            self.write({"error": f"Snippet {snippet_id} not found"})
            return

        try:
            # Register required extensions so save() includes their JS/CSS
            extensions = list(set(find_extensions(snippet.app)))
            if extensions:
                pn.extension(*extensions)

            preamble = "import panel as pn\n\npn.config.design = None\n\n"
            app = preamble + snippet.app
            module_name = f"bokeh_app_embed_{snippet.id.replace('-', '_')}"
            result = None

            if snippet.method == "jupyter":
                # Execute code, evaluate the last expression
                statements, last_expr = extract_last_expression(app)
                namespace = execute_in_module(
                    statements, module_name=module_name, cleanup=False,
                )
                try:
                    result = eval(last_expr, namespace) if last_expr else None  # noqa: S307
                finally:
                    sys.modules.pop(module_name, None)

            else:
                # Panel method — capture .servable() calls
                captured: list = []
                original_servable = pn.viewable.Viewable.servable

                def _capturing_servable(self_inner, *args, **kwargs):
                    captured.append(self_inner)
                    return self_inner

                pn.viewable.Viewable.servable = _capturing_servable  # type: ignore[assignment]
                try:
                    execute_in_module(app, module_name=module_name, cleanup=True)
                finally:
                    pn.viewable.Viewable.servable = original_servable  # type: ignore[assignment]

                if captured:
                    result = pn.Column(*captured) if len(captured) > 1 else captured[0]

            if result is None:
                self.set_status(200)
                self.set_header("Content-Type", "text/html; charset=utf-8")
                self.write(
                    "<!doctype html><html><body style='font-family:system-ui;padding:2em;opacity:.7'>"
                    "<p>Code executed successfully (no output to display).</p>"
                    "</body></html>"
                )
                return

            # Render to static HTML using CDN resources (keeps payload small)
            obj = pn.panel(result, sizing_mode="stretch_width")
            buf = io.StringIO()
            obj.save(buf, resources="cdn", embed=True)
            html = buf.getvalue()

            self.set_status(200)
            self.set_header("Content-Type", "text/html; charset=utf-8")
            self.write(html)

        except Exception as e:
            logger.exception(f"Error rendering embed for snippet {snippet_id}")
            self.set_status(500)
            self.set_header("Content-Type", "application/json")
            self.write({"error": str(e), "traceback": traceback.format_exc()})


class HealthEndpoint(RequestHandler):
    """Tornado RequestHandler for /api/health endpoint."""

    def get(self):
        """Handle GET requests to check server health."""
        self.set_status(200)
        self.set_header("Content-Type", "application/json")
        self.write(
            {
                "status": "healthy",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
