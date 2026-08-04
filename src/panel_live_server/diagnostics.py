"""Carry a snippet's output back out to the caller of ``screenshot``.

The MCP ``screenshot`` tool returns a PNG. Everything else a render produced is
otherwise discarded: what the snippet printed, and what the browser logged while
drawing it. Both matter to an agent.

Losing stdout means an agent that wants to *read* a value has to render it into
the image — a Markdown pane built solely to be screenshotted and then read back
out of a picture. That is a whole extra round-trip for text the process already
had in hand.

Losing the browser console is worse, because it hides a class of failure the
image cannot explain. Bokeh reports layout and tile problems there
(``tile extent is not fully defined``, ``could not set initial ranges``), and a
plot that fails for that reason screenshots as an empty frame — visually
identical to every other cause, so the picture is the least informative evidence
available at exactly the moment it is most tempting to keep taking pictures.

Snippet execution and the ``/api/screenshot`` handler run in the same process,
so an in-memory store keyed by snippet id is sufficient. Nothing here needs to
survive a restart, and entries are consumed once and dropped.
"""

from __future__ import annotations

import base64
import json
import logging
from collections import OrderedDict
from threading import Lock

logger = logging.getLogger(__name__)

#: HTTP header carrying the base64-encoded diagnostics payload alongside the PNG.
#: A header keeps the response body a plain ``image/png``, so nothing that
#: already consumes this endpoint has to change.
HEADER = "X-PLS-Diagnostics"

MAX_ENTRIES = 64
"""Snippets retained before the oldest is evicted."""

MAX_CHARS = 4000
"""Per-stream cap. Enough for a traceback or a run of console errors, small
enough to stay well inside Tornado's header limit once base64-encoded."""

MAX_CONSOLE_LINES = 200
"""Console messages collected per capture, before dropping the rest."""

_store: OrderedDict[str, str] = OrderedDict()
_lock = Lock()


def record(snippet_id: str, text: str) -> None:
    """Store *text* as the captured output of ``snippet_id``.

    Called with whatever the snippet wrote to stdout/stderr. Repeated calls for
    the same id append, so a partial write before an exception is not lost.
    """
    if not snippet_id or not text or not text.strip():
        return
    with _lock:
        merged = _store.get(snippet_id, "") + text
        # Keep a little more than the reported cap so truncation can still show
        # the most recent output rather than an already-clipped middle.
        _store[snippet_id] = merged[-(MAX_CHARS * 2) :]
        _store.move_to_end(snippet_id)
        while len(_store) > MAX_ENTRIES:
            _store.popitem(last=False)


def pop(snippet_id: str) -> str:
    """Return and forget the output recorded for ``snippet_id``."""
    with _lock:
        return _store.pop(snippet_id, "")


def truncate(text: str, limit: int = MAX_CHARS) -> str:
    """Clip *text* to *limit*, keeping the tail.

    The end is the informative part — a traceback's final line, or the last
    thing printed before something went wrong.
    """
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"[… {omitted} earlier characters omitted]\n{text[-limit:]}"


def collapse_repeats(lines: list[str]) -> list[str]:
    """Collapse consecutive identical lines into a single ``(xN)`` entry.

    Browser consoles repeat: one failing tile prefetch can log the same message
    per tile. Without this, a single fault fills the whole budget and crowds out
    the messages that would identify it.
    """
    collapsed: list[str] = []
    for line in lines:
        if collapsed and collapsed[-1][0] == line:
            collapsed[-1][1] += 1
        else:
            collapsed.append([line, 1])
    return [text if count == 1 else f"{text}  (x{count})" for text, count in collapsed]


def build(python_output: str, console_lines: list[str] | None) -> dict[str, str]:
    """Assemble the payload for the response header. Empty dict when nothing ran."""
    payload: dict[str, str] = {}
    if python_output and python_output.strip():
        payload["python"] = truncate(python_output.strip())
    if console_lines:
        joined = "\n".join(collapse_repeats(console_lines))
        if joined.strip():
            payload["console"] = truncate(joined.strip())
    return payload


def encode(payload: dict[str, str]) -> str:
    """Base64-encode *payload* so it is safe to put in an HTTP header."""
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def decode(raw: str) -> dict[str, str]:
    """Inverse of :func:`encode`. Returns ``{}`` rather than raising on junk."""
    if not raw:
        return {}
    try:
        decoded = json.loads(base64.b64decode(raw.encode("ascii")).decode("utf-8"))
    except Exception:
        logger.debug("Could not decode %s header", HEADER, exc_info=True)
        return {}
    return decoded if isinstance(decoded, dict) else {}


def render(payload: dict[str, str]) -> str:
    """Format *payload* as the text block handed to the agent."""
    if not payload:
        return ""
    sections = []
    if payload.get("python"):
        sections.append(f"stdout/stderr from the snippet:\n{payload['python']}")
    if payload.get("console"):
        sections.append(f"browser console:\n{payload['console']}")
    return "\n\n".join(sections)
