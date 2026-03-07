"""Configuration for Panel Live Server."""

import logging
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel
from pydantic import Field

logger = logging.getLogger("panel_live_server")


def _default_user_dir() -> Path:
    return Path(os.getenv("PANEL_LIVE_SERVER_USER_DIR", "~/.panel-live-server")).expanduser()


class Config(BaseModel):
    """Panel Live Server configuration."""

    port: int = Field(default=5005, description="Port for the Panel server")
    host: str = Field(default="localhost", description="Host address for the Panel server")
    max_restarts: int = Field(default=3, description="Maximum number of restart attempts")
    db_path: Path = Field(
        default_factory=lambda: _default_user_dir() / "snippets" / "snippets.db",
        description="Path to SQLite database for snippets",
    )
    jupyter_server_proxy_url: str = Field(default="", description="Jupyter server proxy URL")


_config: Optional[Config] = None


def get_config() -> Config:
    """Get or create the config instance."""
    global _config
    if _config is None:
        _config = Config(
            port=int(os.getenv("PANEL_LIVE_SERVER_PORT", "5005")),
            host=os.getenv("PANEL_LIVE_SERVER_HOST", "localhost"),
            db_path=Path(os.getenv("PANEL_LIVE_SERVER_DB_PATH", str(_default_user_dir() / "snippets" / "snippets.db"))),
            jupyter_server_proxy_url=os.getenv("JUPYTER_SERVER_PROXY_URL", ""),
        )
    return _config


def reset_config() -> None:
    """Reset config (for testing)."""
    global _config
    _config = None
