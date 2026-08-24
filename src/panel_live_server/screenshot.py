"""Headless-browser screenshot capture for rendered Panel snippets.

Wraps Playwright (Chromium) to load a ``/view`` page and capture a PNG so the
MCP ``screenshot`` tool can hand an LLM a picture of the *rendered* output —
the actual layout, fonts, and margins as a user would see them, not just the
source code.

A dashboard is often taller than the browser window, and much of what it can
show is not showing yet. Both are invisible in a picture: a page cut in half
looks exactly like a page that ends, and a chart you have not zoomed into is
absent in the same silent way an empty chart is. So every capture also
*reports* what it did not show — the controls it found on the page, and
whether content continues past the fold (see :class:`Capture`). Both facts are
read off the loaded page for the cost of two locator calls; neither clicks
anything, and neither costs an extra image.

Acting on that report is the caller's choice, never this module's, because
only the caller knows the question. "Is the top chart blue?" needs one picture;
"review my dashboard" needs all of them; "do the points resolve when you zoom
in?" needs a click and a drag first. So ``full_page`` defaults to the cheapest
honest answer, and ``do`` — a short script of clicks, selections, and drags
run before the shutter — is empty unless the caller asks. It carries the same
name at every layer, MCP tool to browser, so there is no point where a reader
has to learn that one thing is called two things.

This module deliberately recognises **no widget at all**. It used to look for
``.bk-tab`` and call what it found "pages", which meant a dashboard built from
a ``Select``, a ``Button``, or a custom tab strip had no pages as far as the
tool was concerned, and a plot you could only read by zooming could not be read
at all. Elements are found by the name a *user* would use — visible text, a
tooltip, an accessible label — or, for a canvas, by nothing but coordinates.

Playwright is a **required** dependency (included in the base install). Import /
launch failures are surfaced as :class:`PlaywrightUnavailableError` with an
install hint so callers can degrade gracefully instead of crashing.
"""

import asyncio
import base64
import json
import logging
import math
import os
import subprocess
import sys
from dataclasses import dataclass, field

from panel_live_server.config import get_config

logger = logging.getLogger(__name__)


class PlaywrightUnavailableError(RuntimeError):
    """Raised when Playwright or its browser is not installed/launchable."""


class ActionError(ValueError):
    """Raised when a step in ``do`` cannot be carried out as written.

    A malformed step, a name that matches nothing, or a name that matches
    several things. All three are the caller's to fix and all three are fixed
    from the message alone, so the message carries what is actually on the page
    rather than only saying no.

    Distinct from every other ``ValueError`` the capture path can raise (a
    malformed width, say) so the HTTP layer can answer "your script was wrong"
    with a 400 without also blaming the caller for bugs of ours.
    """


_INSTALL_HINT = "Playwright's Chromium browser is not installed. Run:\n    pls install-browser"


def install_browser() -> int:
    """Download the headless Chromium browser the screenshot tool needs.

    Playwright ships its browser binary separately from the Python package, so
    ``pip``/``uv`` installs do not fetch it automatically. This shells out to
    ``<this-interpreter> -m playwright install chromium`` so the browser always
    lands in the same environment that is running ``pls`` — avoiding the common
    trap where the binary is installed under a different interpreter.

    Returns
    -------
    int
        The installer subprocess exit code (``0`` on success).
    """
    return subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"]).returncode


def is_browser_installed() -> bool:
    """Return ``True`` if the Chromium binary Playwright needs is present.

    This is a cheap check — it does not launch a browser. It uses Playwright's
    sync API, so call it from a worker thread (e.g. ``asyncio.to_thread``), not
    directly inside a running event loop.
    """
    try:
        # noqa: not about import cost. server.py, client.py, and endpoints.py all
        # import this module at module level, so a broken playwright install would
        # take `pls mcp` down entirely instead of just disabling screenshots.
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            path = p.chromium.executable_path
        return bool(path) and os.path.exists(path)
    except Exception:
        return False


# Best-effort wait for Panel/Bokeh content to mount before capturing.
_CONTENT_SELECTOR = "canvas, .bk-Row, .bk-Column, .bk, .markdown, table, img, svg"

#: Roughly, the things on a page a user could act on. Used **only** to write the
#: report and the "no match" message — never to decide what :func:`_locate` is
#: allowed to reach, which is the whole difference between this and the tab
#: selector it replaces. That one *gated* capture, so markup it did not know
#: about was unreachable; this one is advisory, so markup it does not know about
#: costs a less helpful list and nothing else.
#:
#: Only Playwright's selector engine finds these — Bokeh 3 mounts components
#: into shadow roots, which ``document.querySelectorAll`` does not pierce.
#: ``[title]`` earns its own place in the list, not just its own attribute in
#: :data:`_CONTROL_NAME_JS`: Bokeh's toolbar (Box Zoom, Pan, Wheel Zoom, Save,
#: Reset) renders each tool as a plain ``<div class="bk-OnOffButton">`` with a
#: ``title``, not as a ``<button>`` — without ``[title]`` here the toolbar is
#: invisible to the report even though :func:`_locate` can still click it.
#: ``.bk-tab`` earns the same treatment for the same reason: a native ``pn.Tabs``
#: header is a bare ``<div class="bk-tab">`` with no ``role`` and no ``title``,
#: so nothing else in this list would catch it. Naming this one convention does
#: not revive the old gate — ``do`` still reaches anything by text regardless of
#: whether it is listed here, so a widget this selector does not know about
#: costs a less complete report, never a broken click.
_CONTROL_SELECTOR = (
    "button, a[href], select, summary, input:not([type='hidden']), [title], .bk-tab, "
    "[role=tab], [role=button], [role=menuitem], [role=option], [role=switch], [role=slider]"
)

#: A control's name is whatever a person would call it. A labelled form field
#: is called by its label, not by the option text sitting inside it — a
#: ``<select>`` with a ``<label for=...>`` would otherwise report as the
#: concatenation of its own options. Everything else falls back to its own
#: words, which is where a plain button or a Bokeh toolbar icon (no label, a
#: ``title`` and no text) gets named.
_CONTROL_NAME_JS = """el => (
    (el.labels && el.labels[0] && el.labels[0].textContent.trim())
    || el.getAttribute('aria-label')
    || (el.innerText || el.textContent || '').trim()
    || el.getAttribute('title')
    || el.getAttribute('placeholder')
    || ''
).trim()"""

#: Longest ``do`` script accepted, as a backstop against a runaway loop. Not a
#: judgement about how many steps a real task needs — a dozen is already an
#: unusual amount of driving for one picture.
_MAX_ACTIONS = 20

#: How many control names the report lists before it stops and says how many
#: more there were. A dashboard can have a great many; the point of the line is
#: to give the caller a vocabulary, not an inventory.
_MAX_CONTROLS_REPORTED = 24

#: Intermediate mouse positions in a drag. Bokeh's box-zoom (and every other
#: drag tool) tracks ``mousemove`` between press and release; jumping straight
#: from start to end emits none, and the gesture is silently ignored.
_DRAG_STEPS = 12

#: The element whose scrolling reveals the rest of the page. Usually the
#: document, but Panel templates give ``body`` ``overflow: hidden`` and scroll
#: ``div.main`` instead — so ``window.scrollTo`` moves nothing and
#: ``documentElement.scrollHeight`` reports a single viewport. Picks whichever
#: scrollable element has the most content hidden below its own fold, and
#: descends into shadow roots for the same reason ``_CONTROL_SELECTOR`` needs a
#: Playwright locator. Returns the element itself, so the caller can both
#: measure it and set its ``scrollTop``.
_SCROLLER_JS = """() => {
    const root = document.scrollingElement || document.documentElement;
    let best = root;
    let hidden = root.scrollHeight - root.clientHeight;
    const visit = (node) => {
        for (const el of node.querySelectorAll('*')) {
            const overflowY = getComputedStyle(el).overflowY;
            if (overflowY === 'auto' || overflowY === 'scroll') {
                const overflow = el.scrollHeight - el.clientHeight;
                if (overflow > hidden) { best = el; hidden = overflow; }
            }
            if (el.shadowRoot) visit(el.shadowRoot);
        }
    };
    visit(document);
    return best;
}"""

#: How much content the scrolling element holds, how much of it fits, and where
#: it is scrolled to right now (so the capture can put it back).
_SCROLLER_METRICS_JS = "el => ({content: el.scrollHeight, visible: el.clientHeight, offset: el.scrollTop})"

_SCROLL_TO_JS = "(el, offset) => { el.scrollTop = offset; }"

#: Settle between tiles. Short and independent of the caller's ``settle_ms``:
#: scrolling does not re-lay-out or redraw a Bokeh plot, it only needs long
#: enough for sticky headers and lazily-mounted rows to catch up.
_TILE_SETTLE_MS = 250

#: HTTP header carrying what the capture found — page labels and the tile count
#: — alongside a single PNG, so a caller that got one image still learns what it
#: did not see. A wire contract between ``endpoints`` and ``client``, like
#: ``diagnostics.HEADER``.
META_HEADER = "X-PLS-Capture"

#: Ceiling on the encoded header, well under the ~8 KB a server will accept for
#: one header line. Tab text is user-authored and unbounded; a dashboard whose
#: labels are paragraphs must cost the caller some page names, never the image.
_MAX_META_BYTES = 4096

#: Longest single control name kept. Controls are labelled, not paragraphed —
#: anything past this is a runaway string, and truncating it keeps the *others*.
_MAX_LABEL_CHARS = 80


@dataclass
class Capture:
    """One browser visit: the images taken, and an honest account of the rest.

    ``images`` holds ``(label, png)`` pairs. The label says which screen of a
    tall page this is — ``""`` for the ordinary single-image case, which is most
    of them.

    The remaining fields are the report. ``controls`` names what is on the page
    that could be acted on, which is both the vocabulary for a follow-up ``do``
    and the answer to "what else is there". ``total_tiles`` is how many screens
    of content the page holds and ``captured_tiles`` how many came back;
    ``total_tiles > captured_tiles`` means the picture stops before the content
    does.
    """

    images: list[tuple[str, bytes]] = field(default_factory=list)
    controls: list[str] = field(default_factory=list)
    total_tiles: int = 1
    captured_tiles: int = 1

    @property
    def png(self) -> bytes | None:
        """The first PNG, for callers that only ever wanted one image."""
        return self.images[0][1] if self.images else None


def encode_meta(capture: Capture) -> str:
    """Base64-encode *capture*'s report so it is safe to put in an HTTP header.

    Control names are dropped from the end until the result fits
    ``_MAX_META_BYTES``; the tile counts are fixed-size and always survive.
    A dashboard whose labels are paragraphs must cost the caller some names,
    never the image.
    """
    labels = [label[:_MAX_LABEL_CHARS] for label in capture.controls]
    while True:
        payload = {
            "controls": labels,
            "total_tiles": capture.total_tiles,
            "captured_tiles": capture.captured_tiles,
        }
        encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        if len(encoded) <= _MAX_META_BYTES or not labels:
            return encoded
        labels.pop()


def decode_meta(raw: str) -> dict:
    """Inverse of :func:`encode_meta`. Returns ``{}`` rather than raising on junk."""
    if not raw:
        return {}
    try:
        decoded = json.loads(base64.b64decode(raw.encode("ascii")).decode("utf-8"))
    except Exception:
        logger.debug("Could not decode %s header", META_HEADER, exc_info=True)
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _labels(meta: dict, key: str) -> list[str]:
    value = meta.get(key)
    return [str(label) for label in value] if isinstance(value, list) else []


def apply_meta(capture: Capture, meta: dict) -> Capture:
    """Fill *capture*'s report fields in from a decoded :func:`encode_meta` payload."""
    capture.controls = _labels(meta, "controls")
    try:
        capture.total_tiles = max(1, int(meta.get("total_tiles", 1)))
        capture.captured_tiles = max(1, int(meta.get("captured_tiles", 1)))
    except (TypeError, ValueError):
        capture.total_tiles = capture.captured_tiles = 1
    return capture


def tile_label(index: int, total: int) -> str:
    """Name one image: which screen of a tall page it is. ``""`` when it is the only one."""
    return f"screen {index + 1} of {total}" if total > 1 else ""


#: Every step shape ``do`` accepts, as ``key -> what its value must look like``.
#: Kept as data so the validator and the error message cannot drift apart: the
#: message a caller gets is generated from this table, not written beside it.
_ACTION_SHAPES = {
    "click": '{"click": "<name>"}, optionally with "nth": <int>',
    "select": '{"select": "<name>", "value": "<option>"}',
    "fill": '{"fill": "<name>", "value": "<text>"}',
    "key": '{"key": "<KeyName>"}, e.g. "Enter" or "ArrowRight"',
    "drag": '{"drag": [x0, y0, x1, y1]} in viewport pixels',
    "wait": '{"wait": <milliseconds>}',
}


def _shapes_hint() -> str:
    return "Valid steps are: " + "; ".join(_ACTION_SHAPES.values()) + "."


def check_actions(do: list | None, limit: int = _MAX_ACTIONS) -> list[dict]:
    """Validate a ``do`` script before a browser is involved.

    Every problem here is a typo in the caller's own message, so it is worth
    catching before the cost of a page load — and worth answering with the shape
    that *would* have worked rather than only with what did not.

    Raises
    ------
    ActionError
        If the script is not a list of well-formed single-action steps, or is
        longer than *limit*.
    """
    if not do:
        return []
    if not isinstance(do, list):
        raise ActionError(f"'do' must be a list of steps, not {type(do).__name__}. {_shapes_hint()}")
    if len(do) > limit:
        raise ActionError(f"'do' has {len(do)} steps; at most {limit} are run in one capture. Split the work across calls.")

    checked = []
    for position, step in enumerate(do, start=1):
        if not isinstance(step, dict):
            raise ActionError(f"Step {position} must be an object, not {type(step).__name__}. {_shapes_hint()}")

        verbs = [key for key in step if key in _ACTION_SHAPES]
        if not verbs:
            unknown = ", ".join(repr(key) for key in step) or "nothing"
            raise ActionError(f"Step {position} names no action ({unknown} given). {_shapes_hint()}")
        if len(verbs) > 1:
            raise ActionError(f"Step {position} names {len(verbs)} actions ({', '.join(verbs)}); each step does exactly one thing.")

        verb = verbs[0]
        _check_step(position, verb, step)
        checked.append(step)
    return checked


def _check_step(position: int, verb: str, step: dict) -> None:
    """Check one already-identified step's arguments, raising :class:`ActionError`."""

    def bad(why: str) -> ActionError:
        return ActionError(f"Step {position} ({verb}) {why}. Expected {_ACTION_SHAPES[verb]}.")

    value = step[verb]

    if verb in ("click", "select", "fill"):
        if not isinstance(value, str) or not value.strip():
            raise bad("needs a non-empty name to act on")
        if verb in ("select", "fill") and not isinstance(step.get("value"), str):
            raise bad("needs a string 'value'")
        if verb == "click" and "nth" in step and not isinstance(step["nth"], int):
            raise bad("has a non-integer 'nth'")
    elif verb == "key":
        if not isinstance(value, str) or not value.strip():
            raise bad("needs a key name")
    elif verb == "drag":
        # bool is an int subclass, and `{"drag": [True, 0, 1, 1]}` is a mistake
        # worth naming rather than silently dragging from (1, 0).
        if not isinstance(value, (list, tuple)) or len(value) != 4 or not all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in value):
            raise bad("needs exactly four numbers")
    elif verb == "wait":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise bad("needs a non-negative number of milliseconds")


class _BrowserManager:
    """Lazily launches and reuses a single shared headless Chromium browser."""

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._lock = asyncio.Lock()

    async def _ensure_browser(self):
        """Return a connected browser, launching one on first use."""
        if self._browser is not None and self._browser.is_connected():
            return self._browser

        async with self._lock:
            # Re-check inside the lock — another coroutine may have launched it.
            if self._browser is not None and self._browser.is_connected():
                return self._browser

            try:
                # noqa: same reason as is_browser_installed above. Keeping this
                # nested turns a broken install into PlaywrightUnavailableError
                # rather than an import-time failure of every module downstream.
                from playwright.async_api import async_playwright  # noqa: PLC0415
            except ImportError as e:
                raise PlaywrightUnavailableError(_INSTALL_HINT) from e

            self._playwright = await async_playwright().start()
            try:
                self._browser = await self._playwright.chromium.launch(headless=True)
            except Exception as e:
                await self._stop_playwright()
                raise PlaywrightUnavailableError(f"Failed to launch Chromium: {e}\n{_INSTALL_HINT}") from e

            return self._browser

    async def capture(
        self,
        url: str,
        *,
        width: int,
        height: int,
        full_page: bool,
        settle_ms: int,
        timeout_ms: int,
        do: list | None = None,
        max_tiles: int = 4,
        max_actions: int = _MAX_ACTIONS,
        console_sink: list[str] | None = None,
    ) -> Capture:
        """Load ``url`` in a fresh browser context, run ``do``, and screenshot it.

        When ``console_sink`` is given, browser console messages and uncaught
        page errors are appended to it. Bokeh reports layout and tile failures
        only to the console, so without this a plot that dies client-side is
        indistinguishable in the PNG from one that simply has no data.
        """
        # Before the browser: a malformed script is a typo in the caller's own
        # message, and making them wait for a page load to hear about it is a
        # waste of everyone's time.
        steps = check_actions(do, max_actions)

        browser = await self._ensure_browser()
        context = await browser.new_context(viewport={"width": width, "height": height})
        try:
            page = await context.new_page()

            if console_sink is not None:
                # Resolved per capture, not at import, so reset_config() applies.
                max_lines = get_config().diagnostics_max_console_lines

                # Subscribe before goto, or the messages emitted during initial
                # load — the interesting ones — are missed.
                def _note(text: str) -> None:
                    if len(console_sink) < max_lines:
                        console_sink.append(text)

                page.on("console", lambda msg: _note(f"[{msg.type}] {msg.text}"))
                page.on("pageerror", lambda err: _note(f"[pageerror] {err}"))

            # Use "load" rather than "networkidle": Panel's ``server`` method keeps
            # a live Bokeh websocket open, so the network never goes idle.
            await page.goto(url, wait_until="load", timeout=timeout_ms)

            # Best-effort wait for Panel/Bokeh content to mount.
            try:
                await page.wait_for_selector(_CONTENT_SELECTOR, timeout=min(5000, timeout_ms))
            except Exception:
                logger.debug("No known content selector matched for %s; capturing anyway.", url)

            # Bokeh draws asynchronously after mount; give the canvas time to settle.
            await page.wait_for_timeout(settle_ms)

            for step in steps:
                await self._perform(page, step)
                # A step can re-lay-out and, for Bokeh, redraw from scratch —
                # same reasoning as the settle after the initial load.
                await page.wait_for_timeout(settle_ms)

            tiles = max_tiles if full_page else 1
            shots, total = await self._shoot(page, max_tiles=tiles)
            controls = await self._controls(page)
            return Capture(
                images=[(tile_label(i, len(shots)), png) for i, png in enumerate(shots)],
                controls=controls,
                total_tiles=total,
                captured_tiles=len(shots),
            )
        finally:
            await context.close()

    async def _controls(self, page) -> list[str]:
        """Read-only inventory of what could be acted on, for the report.

        Never used to decide what :meth:`_locate` can reach — only to write the
        report and the "no match" message. Deduplicated and capped so a
        dashboard with hundreds of rows costs the caller a short list, not one
        entry per row.
        """
        try:
            handles = await page.locator(_CONTROL_SELECTOR).all()
        except Exception:
            logger.debug("Could not enumerate controls; reporting none.", exc_info=True)
            return []

        names: list[str] = []
        seen: set[str] = set()
        for handle in handles:
            if len(names) >= _MAX_CONTROLS_REPORTED:
                break
            try:
                name = (await handle.evaluate(_CONTROL_NAME_JS)).strip()
            except Exception:
                continue
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        return names

    async def _locate(self, page, name: str, nth: int | None, tag: str | None = None):
        """Find the single element named *name*, raising :class:`ActionError` otherwise.

        Tries an exact match on visible text, tooltip, accessible label, or
        placeholder first — the ways a person would refer to something, in
        roughly that order — then falls back to a substring match on the same
        four, so a caller does not have to quote a label verbatim. The title
        substring match is why "Box Zoom" reaches Bokeh's actual toolbar button,
        whose title is "Box Zoom (either x, y or both dimensions)" — no
        caller should have to type that in full. An unlabelled text input is
        why placeholder is in the chain at all: it is often the only name the
        field has. Playwright locators pierce shadow roots, which is why this
        finds a Bokeh-mounted control that ``document.querySelectorAll`` could not.

        *tag* narrows the match to elements of that tag (an ``and_`` locator, not
        a CSS combinator). ``select``/``fill`` pass their own tag: a
        ``<label for="Region">`` and the ``<select>`` it names both carry the
        text "Region" — one via ``get_by_text``, one via ``get_by_label`` — and
        without narrowing, "pick Region" would falsely report two matches
        instead of quietly meaning the control.
        """

        def scoped(locator):
            return locator.and_(page.locator(tag)) if tag else locator

        exact = scoped(
            page.get_by_text(name, exact=True)
            .or_(page.get_by_title(name, exact=True))
            .or_(page.get_by_label(name, exact=True))
            .or_(page.get_by_placeholder(name, exact=True))
        )
        candidates = (
            exact
            if await exact.count()
            else scoped(page.get_by_text(name).or_(page.get_by_title(name)).or_(page.get_by_label(name)).or_(page.get_by_placeholder(name)))
        )
        count = await candidates.count()

        if count == 0:
            controls = await self._controls(page)
            available = ", ".join(controls) if controls else "nothing clickable was found on the page"
            raise ActionError(f"No element matches {name!r}. On this page: {available}.")

        if nth is not None:
            if not 0 <= nth < count:
                raise ActionError(f"{name!r} matches {count} elements; 'nth' must be between 0 and {count - 1}.")
            return candidates.nth(nth)

        if count > 1:
            raise ActionError(f'{name!r} matches {count} elements. Use a more specific name, or add "nth": 0..{count - 1} to pick one.')

        return candidates.first

    async def _perform(self, page, step: dict) -> None:
        """Carry out one already-validated ``do`` step."""
        if "click" in step:
            target = await self._locate(page, step["click"], step.get("nth"))
            await target.click()
        elif "select" in step:
            target = await self._locate(page, step["select"], None, tag="select")
            await target.select_option(label=step["value"])
        elif "fill" in step:
            target = await self._locate(page, step["fill"], None, tag="input, textarea")
            await target.fill(step["value"])
        elif "key" in step:
            await page.keyboard.press(step["key"])
        elif "drag" in step:
            x0, y0, x1, y1 = step["drag"]
            await page.mouse.move(x0, y0)
            await page.mouse.down()
            await page.mouse.move(x1, y1, steps=_DRAG_STEPS)
            await page.mouse.up()
        elif "wait" in step:
            await page.wait_for_timeout(step["wait"])

    async def _shoot(self, page, *, max_tiles: int) -> tuple[list[bytes], int]:
        """Capture the loaded *page* as up to ``max_tiles`` viewport-sized images.

        Returns ``(pngs, tiles_the_page_needs)`` — the second number is what
        makes a truncated capture say so instead of looking complete.

        Deliberately *not* Playwright's ``full_page``, and deliberately not the
        grow-the-viewport trick that would make it reach a template's nested
        scroller. Both produce one very tall image, and a very tall image is
        the wrong answer twice over: it is downscaled to a sliver by the time a
        model sees it, so every axis label is unreadable; and growing the
        viewport lets ``sizing_mode="stretch_both"`` plots stretch into it, so
        the picture is of a window nobody has. Scrolling changes neither the
        layout nor the scale, so each tile is the app as rendered.
        """
        scroller = await page.evaluate_handle(_SCROLLER_JS)
        try:
            metrics = await scroller.evaluate(_SCROLLER_METRICS_JS)
            step = max(1, int(metrics["visible"]))
            total = max(1, math.ceil(int(metrics["content"]) / step))

            if total == 1 or max_tiles <= 1:
                return [await page.screenshot(type="png")], total

            shots = []
            for index in range(min(total, max_tiles)):
                # scrollTop clamps, so the last tile lands flush with the bottom
                # and overlaps its neighbour rather than running off the end.
                await scroller.evaluate(_SCROLL_TO_JS, index * step)
                await page.wait_for_timeout(_TILE_SETTLE_MS)
                shots.append(await page.screenshot(type="png"))

            # Leave the next page of a multipage capture starting where this one did.
            await scroller.evaluate(_SCROLL_TO_JS, metrics["offset"])
            return shots, total
        finally:
            await scroller.dispose()

    async def _stop_playwright(self) -> None:
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None


_manager = _BrowserManager()


async def capture_pages(
    url: str,
    *,
    width: int = 1200,
    height: int = 800,
    full_page: bool = False,
    settle_ms: int = 1200,
    timeout_ms: int = 30000,
    do: list | None = None,
    max_tiles: int = 4,
    max_actions: int = _MAX_ACTIONS,
    console_sink: list[str] | None = None,
) -> Capture:
    """Screenshot ``url`` using a shared headless browser.

    Both ``full_page`` and ``do`` default to the cheapest honest answer — one
    image of what is on screen, nothing clicked — and the returned
    :class:`Capture` reports what that left out. Ask for more only when the
    question needs it.

    Parameters
    ----------
    full_page : bool, default False
        Capture the whole scrollable page as a run of viewport-sized tiles
        rather than the single visible screen.
    do : list[dict], optional
        Steps to perform, in order, before capturing — ``{"click": "<name>"}``,
        ``{"select": "<name>", "value": "<option>"}``, ``{"fill": "<name>",
        "value": "<text>"}``, ``{"key": "<KeyName>"}``,
        ``{"drag": [x0, y0, x1, y1]}`` (viewport pixels), or
        ``{"wait": <ms>}``. Names are matched against visible text, a tooltip,
        or an accessible label — whatever a person would call the element.
    max_tiles : int, default 4
        Ceiling on how many tiles one ``full_page`` capture returns.
    max_actions : int, default 20
        Ceiling on how many steps ``do`` may contain.
    console_sink : list[str], optional
        If given, browser console messages and uncaught page errors observed
        during the capture are appended to it.

    Returns
    -------
    Capture
        The images taken, plus the controls found and the tiles not captured.

    Raises
    ------
    PlaywrightUnavailableError
        If Playwright or a launchable browser is not available.
    ActionError
        If ``do`` is malformed, or a step names something the page does not
        have (or has more than one of).
    """
    return await _manager.capture(
        url,
        width=width,
        height=height,
        full_page=full_page,
        settle_ms=settle_ms,
        timeout_ms=timeout_ms,
        do=do,
        max_tiles=max_tiles,
        max_actions=max_actions,
        console_sink=console_sink,
    )


async def capture_png(url: str, **kwargs) -> bytes | None:
    """Capture a single PNG of ``url``. Thin wrapper over :func:`capture_pages`."""
    return (await capture_pages(url, **kwargs)).png
