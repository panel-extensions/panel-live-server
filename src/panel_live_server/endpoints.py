"""REST API endpoints for the Display System.

This module implements Tornado RequestHandler classes that provide
HTTP endpoints for creating visualizations and checking server health.
"""

import json
import logging
import sys
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
            method = request_body.get("method", "inline")
            validated = request_body.get("validated", False)

            # Skip validation if already done by the MCP show tool.
            snippet = db.create_visualization(
                app=code,
                name=name,
                description=description,
                method=method,
                skip_validation=validated,
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
    """Render a snippet's ``/view`` page to a PNG via GET /api/screenshot?id=...

    Loads the live ``/view`` page in a headless browser (Playwright) and returns
    a PNG, giving LLMs a picture of the *rendered* output — layout, fonts, and
    margins as a user would see them. When no browser is installed/launchable
    this returns HTTP 503 with an install hint so the caller can surface a clear
    message instead of failing opaquely.

    Query parameters
    ----------------
    id : str
        Snippet id to render (required).
    width, height : int
        Viewport size in px (default from config).
    full_page : bool
        Capture the full scrollable page rather than just the viewport.
    """

    async def get(self):
        """Capture and return the snippet identified by ``?id=`` as a PNG."""
        from panel_live_server.screenshot import PlaywrightUnavailableError
        from panel_live_server.screenshot import capture_png

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

        config = get_config()
        try:
            width = int(self.get_argument("width", str(config.screenshot_width)))
            height = int(self.get_argument("height", str(config.screenshot_height)))
        except ValueError:
            self.set_status(400)
            self.set_header("Content-Type", "application/json")
            self.write({"error": "width and height must be integers"})
            return
        full_page = self.get_argument("full_page", "false").lower() in ("1", "true", "yes")

        view_url = f"http://{_local_host(config.host)}:{config.port}/view?id={snippet_id}"

        try:
            png = await capture_png(
                view_url,
                width=width,
                height=height,
                full_page=full_page,
                settle_ms=config.screenshot_settle_ms,
                timeout_ms=config.screenshot_timeout_ms,
            )
        except PlaywrightUnavailableError as e:
            self.set_status(503)
            self.set_header("Content-Type", "application/json")
            self.write({"error": "PlaywrightUnavailable", "message": str(e)})
            return
        except Exception as e:
            logger.exception(f"Error capturing screenshot for snippet {snippet_id}")
            self.set_status(500)
            self.set_header("Content-Type", "application/json")
            self.write({"error": str(e), "traceback": traceback.format_exc()})
            return

        self.set_status(200)
        self.set_header("Content-Type", "image/png")
        self.write(png)


class HealthEndpoint(RequestHandler):
    """Tornado RequestHandler for /api/health endpoint."""

    def get(self):
        """Handle GET requests to check server health.

        The payload reports the interpreter running this server (``sys.prefix``
        and ``sys.executable``) so a manager can tell whether a server already
        listening on the port belongs to its own environment before adopting it.
        """
        self.set_status(200)
        self.set_header("Content-Type", "application/json")
        self.write(
            {
                "status": "healthy",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "prefix": sys.prefix,
                "executable": sys.executable,
            }
        )
