"""Streaming /stream page — displays a visualization whose code arrives after page load.

The /stream page is loaded by the MCP App iframe as soon as ``show()`` is called,
before code has been written. It shows a loading indicator and polls the session
store every 300 ms. When ``render()`` pushes code to the session, the callback
executes it and replaces the loading indicator with the result — all over Panel's
WebSocket without a page reload.

Only ``method="inline"`` is supported on the streaming path.
"""

import logging
import sys
import traceback

import panel as pn

from panel_live_server.sessions import get_code
from panel_live_server.utils import execute_in_module
from panel_live_server.utils import extract_last_expression
from panel_live_server.utils import find_extensions

logger = logging.getLogger(__name__)


def _execute_inline(code: str, session_id: str) -> pn.viewable.Viewable:
    """Execute inline-method code and return a Panel component."""
    preamble = "import panel as pn\n\npn.config.design = None\n\n"
    module_name = f"bokeh_app_stream_{session_id.replace('-', '_')}"

    statements, last_expr = extract_last_expression(preamble + code)
    namespace = execute_in_module(statements, module_name=module_name, cleanup=False)
    try:
        result = eval(last_expr, namespace) if last_expr else None  # noqa: S307
    finally:
        sys.modules.pop(module_name, None)

    if result is not None:
        return pn.panel(result, sizing_mode="stretch_width")  # type: ignore[return-value]
    return pn.pane.Markdown("*Code executed successfully (no output to display)*")


def stream_page():
    """Create the /stream page — the streaming iframe entry point for show()."""
    session_id_bytes = pn.state.session_args.get("session_id", [b""])[0]  # type: ignore[call-overload]
    session_id = session_id_bytes.decode("utf-8") if session_id_bytes else ""

    if not session_id:
        return pn.pane.Markdown("# Error\n\nNo session ID provided.")

    pn.extension("codeeditor")

    loading = pn.indicators.LoadingSpinner(
        value=True,
        name="Waiting for visualization…",
        sizing_mode="fixed",
        width=60,
        height=60,
    )
    status = pn.pane.Markdown(
        "*Generating code…*",
        sizing_mode="stretch_width",
        styles={"opacity": "0.6", "text-align": "center"},
    )
    container = pn.Column(
        pn.Column(loading, status, align="center", sizing_mode="stretch_both"),
        sizing_mode="stretch_both",
    )

    def check_for_code():
        code = get_code(session_id)
        if code is None:
            return

        cb.stop()
        container.clear()

        needed = list({"bokeh"} | set(find_extensions(code)))
        pn.extension(*needed)

        try:
            result = _execute_inline(code, session_id)
            container.append(result)
        except Exception as e:
            container.append(
                pn.pane.Markdown(
                    f"# Runtime Error\n\n```\n{traceback.format_exc()}\n```",
                    sizing_mode="stretch_width",
                )
            )
            logger.exception("Error executing streamed code for session %s: %s", session_id, e)

    cb = pn.state.add_periodic_callback(check_for_code, period=300)
    return container


if pn.state.served:
    stream_page().servable()
