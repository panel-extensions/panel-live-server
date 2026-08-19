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

from jinja2 import FileSystemLoader
from jinja2 import nodes
from jinja2.sandbox import SandboxedEnvironment

logger = logging.getLogger(__name__)

_BUILTIN_DIR = Path(__file__).parent / "templates" / "prompts"

INSTRUCTIONS = "instructions.md.j2"
SCREENSHOT = "screenshot.md.j2"

_TEMPLATES = (INSTRUCTIONS, SCREENSHOT)

# Templates addressed by one section name: the screenshot tool keeps a block per call form, but configuring it is one idea to a user.
_SINGLE_SECTION = {SCREENSHOT: "screenshot"}

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
    # Sandboxed because the override text is user-supplied config, not app code.
    return SandboxedEnvironment(loader=FileSystemLoader(str(_BUILTIN_DIR)), keep_trailing_newline=False)


def _blocks_in(template: str) -> list[str]:
    """Return the block names declared by one shipped template."""
    source = (_BUILTIN_DIR / template).read_text(encoding="utf-8")
    return [node.name for node in _build_environment().parse(source).find_all(nodes.Block)]


def known_sections() -> list[str]:
    """Return every section name a --prompts file may set."""
    return [name for t in _TEMPLATES for name in ([_SINGLE_SECTION[t]] if t in _SINGLE_SECTION else _blocks_in(t))]


def _section_for(template: str, block: str) -> str:
    """Return the name a --prompts file uses to address *block*."""
    return _SINGLE_SECTION.get(template, block)


def _builtin_block(template: str, block: str) -> str:
    """Return one block of the shipped *template*, with no overrides applied."""
    tmpl = _build_environment().get_template(template)
    return "".join(tmpl.blocks[block](tmpl.new_context())).strip()


def _layer(default_text: str, entry: tuple[str, str]) -> str:
    """Combine the shipped text for a section with the user's override."""
    mode, user_text = entry
    if mode == _REPLACE:
        return user_text
    return f"{_USER_RULES_HEADER}\n{user_text}\n\n{default_text}"


def _child_template_source(template: str, sections: list[str]) -> str:
    """Generate a template that extends *template* and substitutes *sections*."""
    lines = [f'{{% extends "{template}" %}}']
    lines += [f"{{% block {s} %}}{{{{ {_OVERRIDE_CONTEXT_VAR}['{s}'] }}}}{{% endblock %}}" for s in sections]
    return "\n".join(lines)


def _builtin(template: str, block: str | None = None) -> str:
    """Render *template* with no overrides — the last-resort fallback."""
    if block is not None:
        return _builtin_block(template, block)
    return _build_environment().get_template(template).render().strip()


def render_prompt(template: str, block: str | None = None) -> str:
    """Return *template* (or one *block* of it) with configured overrides applied."""
    # Any misconfiguration falls back to the shipped text rather than blocking startup.
    try:
        overrides = _load_overrides()
        if unknown := [s for s in overrides if s not in known_sections()]:
            _warn(f"Unknown prompt section(s): {', '.join(sorted(unknown))}. Known sections: {', '.join(known_sections())}.")

        if block is not None:
            # Layered directly: Jinja only wires up {{ super() }} for a whole-template render.
            default = _builtin_block(template, block)
            entry = overrides.get(_section_for(template, block))
            return _layer(default, entry) if entry else default

        # A --prompts file addresses every template at once, so keep only this one's sections.
        mine = {s: entry for s, entry in overrides.items() if s in _blocks_in(template)}
        if not mine:
            return _builtin(template)

        texts = {s: _layer(_builtin_block(template, s), entry) for s, entry in mine.items()}
        rendered = _build_environment().from_string(_child_template_source(template, list(mine)))
        return rendered.render(**{_OVERRIDE_CONTEXT_VAR: texts}).strip()
    except Exception as e:
        _warn(f"Could not render the configured prompts ({type(e).__name__}: {e}) — using the built-in prompts.")
        try:
            return _builtin(template, block)
        except Exception:
            logger.exception("Built-in prompt template failed to render")
            raise


def render_instructions() -> str:
    """Return the MCP server instructions with any configured overrides applied."""
    return render_prompt(INSTRUCTIONS)
