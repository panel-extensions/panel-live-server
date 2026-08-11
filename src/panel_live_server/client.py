"""HTTP client for Display Server REST API.

This module provides a client for interacting with the Panel Display Server
via its REST API. The client can be used with either a locally-managed subprocess
or a remote server instance.
"""

import base64
import json
import logging

import requests  # type: ignore[import-untyped]

from panel_live_server import diagnostics
from panel_live_server import screenshot

logger = logging.getLogger(__name__)

# Marks a screenshot failure caused by the missing headless browser rather than by the code.
BROWSER_UNAVAILABLE_PREFIX = "PlaywrightUnavailable: "

#: What the screenshot calls hand back: the capture, an error message, and
#: whatever the render printed or the browser logged.
ScreenshotResult = tuple[screenshot.Capture | None, str | None, dict[str, str]]


class DisplayClient:
    """HTTP client for Display Server REST API.

    This client handles all HTTP communication with the Panel Display Server,
    including health checks and snippet creation. It uses a persistent session
    for connection pooling.
    """

    def __init__(self, base_url: str, timeout: int = 30):
        """Initialize the Display Client.

        Parameters
        ----------
        base_url : str
            Base URL of the Display Server (e.g., "http://localhost:5077")
        timeout : int
            Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def is_healthy(self) -> bool:
        """Check if Display Server is healthy.

        Returns
        -------
        bool
            True if server responds to health check, False otherwise
        """
        try:
            response = self.session.get(f"{self.base_url}/api/health", timeout=2)
            return response.status_code == 200
        except requests.RequestException:
            return False

    def create_snippet(self, code: str, name: str = "", description: str = "", method: str = "inline", validated: bool = False) -> dict:
        """Create a visualization snippet on the Display Server.

        Sends Python code to the server for execution and rendering.

        Parameters
        ----------
        code : str
            Python code to execute
        name : str, optional
            Name for the visualization
        description : str, optional
            Description of the visualization
        method : str, optional
            Execution method ("inline" or "server")
        validated : bool, optional
            When True, signals that the code was already validated and executed
            by the MCP ``show`` tool, so the server can skip its redundant
            storage-time validation and execution.

        Returns
        -------
        dict
            Server response containing either:
            - Success: {"url": str, "id": str, ...}
            - Error: {"error": str, "message": str, "traceback": str}

        Raises
        ------
        RuntimeError
            If HTTP request fails
        """
        try:
            response = self.session.post(
                f"{self.base_url}/api/snippet",
                json={
                    "code": code,
                    "name": name,
                    "description": description,
                    "method": method,
                    "validated": validated,
                },
                timeout=self.timeout,
            )

            response.raise_for_status()
            return response.json()

        except requests.RequestException as e:
            logger.exception(f"Error creating visualization: {e}")
            raise RuntimeError(f"Failed to create visualization: {e}") from e

    def evaluate(self, code: str) -> dict:
        """Execute *code* on the server and return its text output.

        Returns
        -------
        dict
            ``{"stdout": str, "result": str, "error": str, "traceback": str}``, or
            ``{"error": ..., "message": ...}`` if the request itself failed.
        """
        try:
            response = self.session.post(
                f"{self.base_url}/api/evaluate",
                json={"code": code},
                timeout=max(self.timeout, 60),
            )
        except requests.RequestException as e:
            logger.warning("Evaluate request error: %s", e)
            return {"error": "RequestException", "message": f"Evaluate request failed: {e}"}

        try:
            return response.json()
        except ValueError:
            return {
                "error": f"HTTP {response.status_code}",
                "message": response.text or "Evaluate returned a non-JSON response.",
            }

    def get_screenshot(
        self,
        snippet_id: str,
        width: int | None = None,
        height: int | None = None,
        full_page: bool = False,
        do: list | None = None,
    ) -> ScreenshotResult:
        """Fetch a screenshot of a snippet's rendered ``/view`` page.

        Returns
        -------
        ScreenshotResult
            ``(capture, None, diagnostics)`` on success, or
            ``(None, error_message, {})`` on failure.
        """
        # A query string cannot carry a list, so `do` rides as JSON text — the
        # server decodes it back on the way in.
        params: dict[str, str | int] = {"id": snippet_id, "full_page": str(full_page).lower(), "do": json.dumps(do) if do else ""}
        if width:
            params["width"] = width
        if height:
            params["height"] = height

        return self._screenshot_request("GET", snippet_id, params=params)

    def screenshot_code(
        self,
        code: str,
        name: str = "",
        description: str = "",
        method: str = "inline",
        width: int | None = None,
        height: int | None = None,
        full_page: bool = False,
        do: list | None = None,
    ) -> ScreenshotResult:
        """Render *code* and return a screenshot of it without keeping the snippet.

        Backs the draft path of the MCP ``screenshot`` tool (issue #43): the
        server stores the code only long enough to load it in a browser, so an
        agent can review a draft without it appearing in the user's feed.

        Returns
        -------
        ScreenshotResult
            ``(capture, None, diagnostics)`` on success, or
            ``(None, error_message, {})`` on failure.
        """
        payload: dict[str, str | int | bool | list | None] = {
            "code": code,
            "name": name,
            "description": description,
            "method": method,
            "full_page": full_page,
            "do": do,
        }
        if width:
            payload["width"] = width
        if height:
            payload["height"] = height

        return self._screenshot_request("POST", "draft", json=payload)

    def _screenshot_request(self, verb: str, label: str, **kwargs) -> ScreenshotResult:
        """Call ``/api/screenshot`` and unpack the image-or-error response.

        One image comes back as ``image/png``; several — tiles of a tall page,
        or pages of a dashboard — come back as JSON carrying a base64 PNG each.
        Either way the report of what was *not* captured rides along in the
        ``X-PLS-Capture`` header.

        Returns
        -------
        ScreenshotResult
            ``(capture, error, diagnostics)``. The last element holds whatever
            the render printed and whatever the browser logged; it is ``{}`` when
            the render was silent or the request failed.
        """
        try:
            response = self.session.request(
                verb,
                f"{self.base_url}/api/screenshot",
                timeout=max(self.timeout, 60),
                **kwargs,
            )
        except requests.RequestException as e:
            logger.warning("Screenshot request error for %s: %s", label, e)
            return None, f"Screenshot request failed: {e}", {}

        if response.status_code == 200:
            content_type = response.headers.get("Content-Type", "")
            meta = screenshot.decode_meta(response.headers.get(screenshot.META_HEADER, ""))
            notes = diagnostics.decode(response.headers.get(diagnostics.HEADER, ""))
            if "image/png" in content_type:
                capture = screenshot.Capture(images=[("", response.content)])
                return screenshot.apply_meta(capture, meta), None, notes
            if "application/json" in content_type:
                try:
                    images = [(i.get("label", ""), base64.b64decode(i["png"])) for i in response.json()["images"]]
                except Exception as e:
                    logger.warning("Malformed multi-image screenshot response for %s: %s", label, e)
                    return None, f"Malformed multi-image screenshot response: {e}", {}
                return screenshot.apply_meta(screenshot.Capture(images=images), meta), None, notes

        try:
            body = response.json()
            message = body.get("message") or body.get("error") or response.text
            # Tag the one failure a caller cannot fix by changing the code, so it
            # can be reported rather than retried.
            if body.get("error") == "PlaywrightUnavailable":
                message = f"{BROWSER_UNAVAILABLE_PREFIX}{message}"
        except ValueError:
            message = response.text or f"HTTP {response.status_code}"
        logger.warning("Screenshot failed (HTTP %s) for %s: %s", response.status_code, label, message)
        return None, message, {}

    def close(self) -> None:
        """Close the HTTP session and cleanup resources."""
        if self.session:
            self.session.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
