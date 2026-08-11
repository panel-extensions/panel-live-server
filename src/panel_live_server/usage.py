"""Count how much code each tool actually receives, so issue #58 can be settled with numbers.

The argument for the draft/promote/edit rework is a cost argument: that a review
loop was sending the same snippet several times over, and executing it twice per
picture. That is measurable, and until it is measured it is only a claim.

Counting happens here, in the display-server process, rather than in the MCP
process, for two reasons. Everything arrives here anyway — every tool ends up
posting its code to an endpoint — and this process outlives any single tool call,
so the totals accumulate across a whole session the way ``_validation_cache``
does. The MCP process could count its own arguments, but it has no obvious place
to report them from.

Deliberately NOT reported in the ``show`` payload. That payload is a message the
model reads in full, and dropping the code echo from it was the point of the
previous step; adding a telemetry block would spend context to measure context.
``/api/health`` is a plain GET nobody pays for.

Nothing here is persisted. A restart is a new session and the numbers start over,
which is the intended granularity: the question is what one working session
costs, not what the tool has cost forever. Note that an adopted server — one
already listening when a new MCP session starts — keeps its existing counts, so
read ``since`` rather than assuming the totals began with the current session.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from datetime import timezone
from threading import Lock

logger = logging.getLogger(__name__)

#: Tool label -> total characters of code received under it.
_chars: defaultdict[str, int] = defaultdict(int)

#: Tool label -> number of calls.
_calls: defaultdict[str, int] = defaultdict(int)

_started_at = datetime.now(timezone.utc)
_lock = Lock()


def record(tool: str, chars: int) -> None:
    """Note that *tool* received *chars* characters of code.

    A call with ``chars=0`` still counts as a call. That case is the point of the
    measurement rather than a degenerate one: a promotion moves a finished
    visualization to the user while sending no code at all, and it is only
    visible as a saving if those calls are counted alongside the ones that do
    carry a payload.

    Parameters
    ----------
    tool : str
        Label for the call site, e.g. ``"show"`` or ``"screenshot"``.
    chars : int
        Characters of code carried by this call.
    """
    with _lock:
        _chars[tool] += max(0, chars)
        _calls[tool] += 1


def snapshot() -> dict:
    """Return the current counts, safe to serialize into a response.

    Returns
    -------
    dict
        ``{"since": iso8601, "total_chars": int, "total_calls": int,
        "by_tool": {tool: {"chars": int, "calls": int}}}``
    """
    with _lock:
        by_tool = {tool: {"chars": _chars[tool], "calls": _calls[tool]} for tool in sorted(_calls)}
        return {
            "since": _started_at.isoformat(),
            "total_chars": sum(_chars.values()),
            "total_calls": sum(_calls.values()),
            "by_tool": by_tool,
        }


def reset() -> None:
    """Clear the counters. For tests, and for starting a fresh measurement."""
    global _started_at
    with _lock:
        _chars.clear()
        _calls.clear()
        _started_at = datetime.now(timezone.utc)
