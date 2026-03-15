"""Panel server for code visualization.

This module implements a Panel web server that executes Python code
and displays the results through various endpoints.
"""

import logging
from urllib.parse import urlparse

from panel_live_server.config import get_config
from panel_live_server.endpoints import HealthEndpoint
from panel_live_server.endpoints import SnippetEndpoint

logger = logging.getLogger(__name__)


def _display_url(address: str, port: int, endpoint: str) -> str:
    """Generate the display server URL, externalizing when config.external_url is set."""
    external_url = get_config().external_url
    if external_url:
        return f"{external_url.rstrip('/')}/{endpoint}"
    return f"http://{address}:{port}/{endpoint}"


def _api_url(address: str, port: int, endpoint: str) -> str:
    """Generate the API URL for a given endpoint."""
    return f"http://{address}:{port}{endpoint}"


def _build_websocket_origins(address: str, port: int) -> list[str]:
    """Build a targeted websocket origin allowlist for Bokeh/Panel.

    Bokeh expects origins as host[:port] values. We include local defaults,
    the configured bind address, and (when configured) the externally reachable
    host from ``external_url``.
    """
    origins: set[str] = {
        f"localhost:{port}",
        f"127.0.0.1:{port}",
    }

    # Add the configured bind address when it is a concrete host.
    if address and address not in {"0.0.0.0", "::"}:
        origins.add(f"{address}:{port}")

    external_url = get_config().external_url
    if external_url:
        parsed = urlparse(external_url)
        if parsed.hostname:
            if parsed.port:
                origins.add(f"{parsed.hostname}:{parsed.port}")
            else:
                origins.add(parsed.hostname)
                if parsed.scheme == "https":
                    origins.add(f"{parsed.hostname}:443")
                elif parsed.scheme == "http":
                    origins.add(f"{parsed.hostname}:80")

    return sorted(origins)


def main(address: str = "localhost", port: int = 5077, show: bool = True) -> None:
    """Start the Panel server."""
    import panel as pn

    from panel_live_server.database import get_db
    from panel_live_server.pages import add_page
    from panel_live_server.pages import admin_page
    from panel_live_server.pages import feed_page
    from panel_live_server.pages import view_page

    # Initialize the database
    _ = get_db()

    # Configure Panel defaults
    pn.template.FastListTemplate.param.main_layout.default = None
    pn.pane.Markdown.param.disable_anchors.default = True

    # Initialize views cache for feed page
    pn.state.cache["views"] = {}

    # Configure pages
    pages = {
        "/view": view_page,
        "/feed": feed_page,
        "/admin": admin_page,
        "/add": add_page,
    }

    # Configure extra patterns for Tornado handlers (REST API endpoints)
    extra_patterns = [
        (r"/api/snippet", SnippetEndpoint),
        (r"/api/health", HealthEndpoint),
    ]

    # Log startup information
    logger.info(f"Starting Panel Live Server at http://{address}:{port}")
    logger.info(f"  Feed:   {_display_url(address, port, 'feed')}")
    logger.info(f"  Add:    {_display_url(address, port, 'add')}")
    logger.info(f"  Admin:  {_display_url(address, port, 'admin')}")
    logger.info(f"  Health: {_api_url(address, port, '/api/health')}")

    # Start server
    pn.serve(
        pages,
        port=port,
        address=address,
        show=show,
        title="Panel Live Server",
        extra_patterns=extra_patterns,
        websocket_origin=_build_websocket_origins(address=address, port=port),
    )


if __name__ == "__main__":
    # Read config from env vars when run as subprocess
    from panel_live_server.config import reset_config

    reset_config()
    config = get_config()
    main(address=config.host, port=config.port, show=False)
