"""Render the prompts sent to the model, with user overrides (issue #50).

Every word the server puts in front of the model used to be a hardcoded string in
``server.py``: the instructions FastMCP sends at startup, and the reminders the
``screenshot`` tool returns alongside an image. Teams have real reasons to change
that text — "we're a Plotly shop, stop recommending hvPlot", or a house style for
how links are presented — and the only way to do it was to fork the repo.

The text now lives in ``templates/prompts/*.md.j2``, carved into named
``{% block %}`` sections. A user points ``pls mcp --prompts`` at a JSON file naming
the sections they want to change::

    {"library_selection": "Always use ECharts. For sine waves use hvplot, in pink."}

A bare string is **added in front of** the shipped text, under a header marking it
as authoritative. That is the safe default: replacing a section wholesale silently
drops operational detail the model depends on (that Matplotlib and Plotly may not
be installed, for instance), and most house rules are additions rather than
deletions. To discard the default text instead, ask for it explicitly::

    {"library_selection": {"replace": "Use plotly.express and nothing else."}}

Sections they do not mention keep rendering from the shipped template, so an
upstream improvement to a section they never touched still reaches them on the
next upgrade. That is the whole reason for blocks rather than "copy the prompt
and edit it".

A broken override must never stop the MCP server from starting: the model losing
a customization is a much smaller problem than the user losing the server. Every
failure below is caught, reported on stderr, and falls back to the shipped text.
"""

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_BUILTIN_DIR = Path(__file__).parent / "templates" / "prompts"

INSTRUCTIONS = "instructions.md.j2"
DRAFT_REVIEW = "draft_review.md.j2"
SHOWN_IMAGE = "shown_image.md.j2"

_TEMPLATES = (INSTRUCTIONS, DRAFT_REVIEW, SHOWN_IMAGE)

# The CLI writes --prompts here before rendering, so flag and env resolve through one path.
_PROMPTS_FILE_ENV = "PANEL_LIVE_SERVER_PROMPTS_FILE"

# Override text is passed as context data so a stray "{{" in someone's prose is never parsed.
_OVERRIDE_CONTEXT_VAR = "_prompt_overrides"

# Keeping both texts routinely contradicts, so this names the source and settles the conflict.
_USER_RULES_HEADER = (
    "USER CONFIGURATION — set by the person running this server. These rules are authoritative: "
    "follow them exactly. Where they conflict with the general guidance below, these win. "
    "That guidance still applies to anything these rules do not cover."
)

_ADD, _REPLACE = "add", "replace"


def _warn(message: str) -> None:
    """Report a misconfiguration without taking the server down with it."""
    print(f"\n  panel-live-server: {message}\n", file=sys.stderr, flush=True)  # noqa: T201
    logger.warning(message)


def _coerce_override(path: Path, section: str, value) -> tuple[str, str] | None:
    """Normalise one JSON value into ``(mode, text)``, or ``None`` if unusable."""
    # Bare strings add rather than replace: replacing silently drops detail the model needs.
    if isinstance(value, str):
        return _ADD, value

    if isinstance(value, dict):
        modes = [m for m in (_ADD, _REPLACE) if m in value]
        if len(modes) != 1:
            _warn(f"--prompts file {path}: section '{section}' must have exactly one of 'add' or 'replace' — ignoring it.")
            return None
        mode = modes[0]
        if not isinstance(text := value[mode], str):
            _warn(f"--prompts file {path}: section '{section}' {mode} must be a string, got {type(text).__name__} — ignoring it.")
            return None
        return mode, text

    _warn(f"--prompts file {path}: section '{section}' must be a string or an object, got {type(value).__name__} — ignoring it.")
    return None


def _load_overrides() -> dict[str, tuple[str, str]]:
    """Read the section overrides, as ``{section: (mode, text)}``."""
    raw_path = os.getenv(_PROMPTS_FILE_ENV, "").strip()
    if not raw_path:
        return {}

    path = Path(raw_path).expanduser()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        _warn(f"Could not read --prompts file {path}: {e} — using the built-in prompts.")
        return {}

    try:
        parsed = json.loads(raw)
    except ValueError as e:
        _warn(f"--prompts file {path} is not valid JSON: {e} — using the built-in prompts.")
        return {}

    if not isinstance(parsed, dict):
        _warn(f"--prompts file {path} must be a JSON object of section → text — using the built-in prompts.")
        return {}

    overrides = {}
    for section, value in parsed.items():
        if entry := _coerce_override(path, section, value):
            overrides[section] = entry
    return overrides


def _build_environment():
    """Build the Jinja environment that loads the shipped templates."""
    from jinja2 import FileSystemLoader
    from jinja2.sandbox import SandboxedEnvironment

    # Sandboxed because the override text is user-supplied config, not app code.
    return SandboxedEnvironment(loader=FileSystemLoader(str(_BUILTIN_DIR)), keep_trailing_newline=False)


def _blocks_in(template: str) -> list[str]:
    """Return the block names declared by one shipped template."""
    from jinja2 import nodes

    source = (_BUILTIN_DIR / template).read_text(encoding="utf-8")
    return [node.name for node in _build_environment().parse(source).find_all(nodes.Block)]


def known_sections() -> list[str]:
    """Return every section name a --prompts file may set."""
    return [section for template in _TEMPLATES for section in _blocks_in(template)]


def _child_template_source(template: str, overrides: dict[str, tuple[str, str]]) -> str:
    """Generate a template that extends *template* and applies *overrides*."""
    # super() is Jinja's name for the parent block, so "add" puts the user's rules in front of it.
    lines = [f'{{% extends "{template}" %}}']
    for section, (mode, _) in overrides.items():
        text = f"{{{{ {_OVERRIDE_CONTEXT_VAR}['{section}'] }}}}"
        body = text if mode == _REPLACE else f"{_USER_RULES_HEADER}\n{text}\n\n{{{{ super() }}}}"
        lines.append(f"{{% block {section} %}}{body}{{% endblock %}}")
    return "\n".join(lines)


def _builtin(template: str) -> str:
    """Render *template* with no overrides — the last-resort fallback."""
    return _build_environment().get_template(template).render().strip()


def render_prompt(template: str) -> str:
    """Return *template* rendered with any configured overrides applied."""
    # Any misconfiguration falls back to the shipped text rather than blocking startup.
    try:
        overrides = _load_overrides()
        if unknown := [s for s in overrides if s not in known_sections()]:
            _warn(f"Unknown prompt section(s): {', '.join(sorted(unknown))}. Known sections: {', '.join(known_sections())}.")

        # A --prompts file addresses every template at once, so keep only the
        # sections this one actually declares.
        mine = {s: entry for s, entry in overrides.items() if s in _blocks_in(template)}
        if not mine:
            return _builtin(template)

        texts = {section: text for section, (_, text) in mine.items()}
        rendered = _build_environment().from_string(_child_template_source(template, mine))
        return rendered.render(**{_OVERRIDE_CONTEXT_VAR: texts}).strip()
    except Exception as e:
        _warn(f"Could not render the configured prompts ({type(e).__name__}: {e}) — using the built-in prompts.")
        try:
            return _builtin(template)
        except Exception:
            logger.exception("Built-in prompt template failed to render")
            raise


def render_instructions() -> str:
    """Return the MCP server instructions with any configured overrides applied."""
    return render_prompt(INSTRUCTIONS)
