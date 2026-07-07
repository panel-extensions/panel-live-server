"""In-process session store for streaming show sessions.

Each streaming session reserves a slot in the Panel server for a visualization
that has not yet received its code. The MCP ``show`` tool creates the session;
the ``render`` tool later pushes the code, which the running Panel /stream page
picks up via periodic polling.
"""

import threading
import time
import uuid
from typing import Optional

_sessions: dict[str, dict] = {}
_lock = threading.Lock()
_SESSION_TTL = 1800  # 30 minutes


def create_session(method: str = "inline") -> str:
    """Create a new streaming session and return its ID."""
    session_id = str(uuid.uuid4())
    with _lock:
        _sessions[session_id] = {
            "method": method,
            "code": None,
            "created_at": time.time(),
        }
    return session_id


def push_code(session_id: str, code: str) -> bool:
    """Push code to an existing session. Returns False if session not found."""
    with _lock:
        if session_id not in _sessions:
            return False
        _sessions[session_id]["code"] = code
        return True


def get_code(session_id: str) -> Optional[str]:
    """Return the code for a session, or None if not yet received."""
    with _lock:
        session = _sessions.get(session_id)
        return session["code"] if session else None


def expire_old_sessions() -> int:
    """Remove sessions older than TTL. Returns count removed."""
    cutoff = time.time() - _SESSION_TTL
    with _lock:
        expired = [sid for sid, s in _sessions.items() if s["created_at"] < cutoff]
        for sid in expired:
            del _sessions[sid]
    return len(expired)
