"""Configuration for Panel Live Server."""

import logging
import os
from pathlib import Path

from pydantic import BaseModel
from pydantic import Field

logger = logging.getLogger("panel_live_server")


def _default_user_dir() -> Path:
    return Path(os.getenv("PANEL_LIVE_SERVER_USER_DIR", "~/.panel-live-server")).expanduser()


def _resolve_external_url(port: int) -> str:
    """Resolve the external URL for the Panel server.

    Checks in priority order:
    1. ``PANEL_LIVE_SERVER_EXTERNAL_URL``                          — explicit override (port-inclusive).
    2. ``JUPYTERHUB_HOST`` + ``JUPYTERHUB_SERVICE_PREFIX``         — JupyterHub with jupyter-server-proxy.
       Note: ``JUPYTERHUB_SERVICE_PREFIX`` is set automatically by JupyterHub, but ``JUPYTERHUB_HOST`` is
       only set automatically in subdomain routing mode and must be supplied manually in path-based routing.
    3. ``CODESPACE_NAME``                                          — GitHub Codespaces port-forwarding URL.
    4. ``""``                                                      — local; callers fall back to ``http://localhost:{port}``.
    """
    if explicit := os.getenv("PANEL_LIVE_SERVER_EXTERNAL_URL", ""):
        return explicit.rstrip("/")

    hub_host = os.getenv("JUPYTERHUB_HOST", "")
    hub_prefix = os.getenv("JUPYTERHUB_SERVICE_PREFIX", "")
    if hub_host and hub_prefix:
        if not hub_host.startswith(("http://", "https://")):
            hub_host = f"https://{hub_host}"
        return f"{hub_host.rstrip('/')}{hub_prefix}proxy/{port}"

    if codespace := os.getenv("CODESPACE_NAME", ""):
        domain = os.getenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN") or "app.github.dev"
        return f"https://{codespace}-{port}.{domain}"

    return ""


class Config(BaseModel):
    """Panel Live Server configuration."""

    port: int = Field(default=5077, description="Port for the Panel server")
    host: str = Field(default="localhost", description="Host address for the Panel server")
    max_restarts: int = Field(default=3, description="Maximum number of restart attempts")
    db_path: Path = Field(
        default_factory=lambda: _default_user_dir() / "snippets" / "snippets.db",
        description="Path to SQLite database for snippets",
    )
    external_url: str = Field(
        default="",
        description=(
            "Externally reachable base URL for the Panel server (port-inclusive). "
            "Auto-detected from JUPYTERHUB_HOST + JUPYTERHUB_SERVICE_PREFIX (JupyterHub) "
            "or CODESPACE_NAME (GitHub Codespaces) if not set explicitly via PANEL_LIVE_SERVER_EXTERNAL_URL."
        ),
    )


_config: Config | None = None


def get_config() -> Config:
    """Get or create the config instance."""
    global _config
    if _config is None:
        port = int(os.getenv("PANEL_LIVE_SERVER_PORT", "5077"))
        _config = Config(
            port=port,
            host=os.getenv("PANEL_LIVE_SERVER_HOST", "localhost"),
            max_restarts=int(os.getenv("PANEL_LIVE_SERVER_MAX_RESTARTS", "3")),
            db_path=Path(os.getenv("PANEL_LIVE_SERVER_DB_PATH", str(_default_user_dir() / "snippets" / "snippets.db"))),
            external_url=_resolve_external_url(port),
        )
    return _config


def reset_config() -> None:
    """Reset config (for testing)."""
    global _config
    _config = None
