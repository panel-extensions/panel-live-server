"""Panel Live Server - Execute and visualize Python code snippets."""

import importlib.metadata
import warnings

try:
    __version__ = importlib.metadata.version("panel-live-server")
except importlib.metadata.PackageNotFoundError as e:  # pragma: no cover
    warnings.warn(f"Could not determine version of panel-live-server\n{e!s}", stacklevel=2)
    __version__ = "unknown"

__all__: list[str] = [
    "__version__",
]
