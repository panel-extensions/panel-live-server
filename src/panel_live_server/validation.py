"""Code validation pipeline for panel-live-server.

Provides four static validation layers that run before code is stored:

- Layer 1  ``ast_check``       — syntax via ``ast.parse()``
- Layer 2  ``ruff_check``      — security rules via ``ruff`` (raises ``SecurityError``)
- Layer 3  ``check_packages``  — all imports are installed
- Formatting  ``ruff_format``  — autoformat via ``ruff format``

Runtime execution (Layer 5) lives in ``utils.validate_code``.
"""

import ast
import importlib.util
import json
import logging
import subprocess
import sys

from fastmcp.exceptions import ToolError

logger = logging.getLogger(__name__)

_RUFF_TIMEOUT_SECONDS = 5

_RUFF_SELECT = "F821," "S102," "S103," "S104," "S108," "S113," "S202," "S301," "S302," "S306," "S307," "S310," "S323," "S501," "S506," "S602," "S605," "S608"

# Import name → PyPI install name.
# Key   = top-level name used in `import <key>` or `from <key> import ...`
# Value = the package name to show in error messages / pip install instructions.
IMPORT_TO_PACKAGE: dict[str, str] = {
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "cv2": "opencv-python",
    "skimage": "scikit-image",
    "bs4": "beautifulsoup4",
    "yaml": "PyYAML",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "gi": "PyGObject",
    "wx": "wxPython",
    "Crypto": "pycryptodome",
    "OpenSSL": "pyOpenSSL",
    "usb": "pyusb",
    "serial": "pyserial",
    "magic": "python-magic",
    "attr": "attrs",
}


class ValidationError(ToolError):
    """Raised by show() when code fails a non-security validation check.

    Covers syntax errors, missing packages, and missing Panel extension declarations.
    The message always begins with the layer name in brackets, e.g.
    ``[syntax] invalid syntax`` so the LLM can identify the failing check at a glance.
    """


class SecurityError(ToolError):
    """Raised by show() when code contains a security violation.

    Given a special class (separate from ValidationError) to signal seriousness —
    security violations are never auto-fixable and should not be retried without
    a substantive code rewrite. Particularly relevant in enterprise contexts where
    security policy enforcement is audited.
    """


# Imports that are categorically blocked regardless of how they are used.
# These modules enable deserialization, low-level system access, or network
# operations that have no place in a visualization snippet.
BLOCKED_IMPORTS: frozenset[str] = frozenset(
    {
        "pickle",
        "marshal",
        "shelve",
        "subprocess",
        "multiprocessing",
        "threading",
        "socket",
        "ctypes",
        "importlib",
        "ftplib",
        "smtplib",
        "telnetlib",
        "webbrowser",
        "xmlrpc",
    }
)


def ast_check(code: str) -> str | None:
    r"""Return an error string if *code* has a syntax error, else ``None``.

    Parameters
    ----------
    code : str
        Python source to check.

    Returns
    -------
    str | None
        Human-readable error with line/col info, or ``None`` if syntax is valid.

    Examples
    --------
    >>> ast_check("x = 1")
    >>> ast_check("if True")
    'expected \':\' (line 1, col 8)'
    """
    try:
        ast.parse(code)
    except SyntaxError as exc:
        return f"{exc.msg} (line {exc.lineno}, col {exc.offset})"
    return None


def ruff_check(code: str) -> None:
    """Run import blocklist and ruff security checks on *code*.

    First performs a fast AST-based blocked-import scan (does not depend on
    ruff being installed), then runs ruff for deeper static analysis.

    Raises ``SecurityError`` if any violations are found.
    Returns ``None`` silently if the code is clean or ruff is not installed.

    Parameters
    ----------
    code : str
        Python source to check.

    Raises
    ------
    SecurityError
        If a blocked import is found or ruff reports any violations.

    Examples
    --------
    >>> ruff_check("x = 1")
    >>> ruff_check("import pickle")  # doctest: +SKIP
    SecurityError: ...
    """
    # --- AST blocked-import scan (always runs, no ruff dependency) -----------
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in BLOCKED_IMPORTS:
                        raise SecurityError(f"line {node.lineno}: import '{alias.name}' is not allowed " f"in visualization code.")
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                if top in BLOCKED_IMPORTS:
                    raise SecurityError(f"line {node.lineno}: 'from {node.module} import ...' is not allowed " f"in visualization code.")
    except SecurityError:
        raise
    except SyntaxError:
        pass  # Layer 1 handles syntax errors

    # --- ruff static analysis -------------------------------------------------
    try:
        result = subprocess.run(  # noqa: S603
            [
                "ruff",
                "check",
                "--select",
                _RUFF_SELECT,
                "--no-fix",
                "--output-format",
                "json",
                "--stdin-filename",
                "snippet.py",
                "-",
            ],
            capture_output=True,
            text=True,
            input=code,
            timeout=_RUFF_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        logger.warning("ruff not found — security check skipped")
        return
    except subprocess.TimeoutExpired:
        logger.warning("ruff timed out after %ss — security check skipped", _RUFF_TIMEOUT_SECONDS)
        return

    if result.returncode == 0 or not result.stdout.strip():
        return

    try:
        diagnostics: list[dict] = json.loads(result.stdout)
    except json.JSONDecodeError:
        return

    errors: list[str] = []
    for diag in diagnostics:
        loc = diag.get("location", {})
        errors.append(f"line {loc.get('row', '?')}: {diag.get('message', '')}")

    if errors:
        raise SecurityError("\n".join(errors))


def check_packages(code: str) -> str | None:
    r"""Check that all packages imported by *code* are installed.

    Parses imports via AST and calls ``importlib.util.find_spec()`` for each
    top-level module name. Stdlib modules are skipped. Returns an error string
    for the first missing package, or ``None`` if everything is available.

    Parameters
    ----------
    code : str
        Python source to analyse.

    Returns
    -------
    str | None
        Error string with install hint, or ``None`` if all packages are available.

    Examples
    --------
    >>> check_packages("import os\\nimport json") is None
    True
    >>> check_packages("import numpy") is None  # doctest: +SKIP
    True
    >>> "list_packages" in (check_packages("import _totally_fake_pkg_xyz") or "")
    True
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None  # Layer 1 handles syntax errors

    stdlib: frozenset[str] = frozenset(sys.stdlib_module_names)

    top_level: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.add(node.module.split(".")[0])

    for import_name in sorted(top_level):
        if import_name in stdlib:
            continue
        if importlib.util.find_spec(import_name) is None:
            package_name = IMPORT_TO_PACKAGE.get(import_name, import_name)
            return (
                f"Package '{package_name}' is not installed in this environment. "
                f"Do NOT attempt to install packages or change the Python environment — "
                f"the environment is fixed and cannot be modified. "
                f"Call list_packages to see what IS available, "
                f"then rewrite the code using an installed library."
            )

    return None


def ruff_format(code: str) -> str:
    r"""Autoformat *code* via ``ruff format`` and return the result.

    Returns *code* unchanged if ruff is not installed or formatting fails.

    Parameters
    ----------
    code : str
        Python source to format.

    Returns
    -------
    str
        Formatted source, or original source on failure.

    Examples
    --------
    >>> ruff_format("x=1+2") == "x = 1 + 2\\n"  # doctest: +SKIP
    True
    """
    try:
        result = subprocess.run(  # noqa: S603
            ["ruff", "format", "--stdin-filename", "snippet.py", "-"],  # noqa: S607
            capture_output=True,
            text=True,
            input=code,
            timeout=_RUFF_TIMEOUT_SECONDS,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return code
    return result.stdout if result.returncode == 0 else code
