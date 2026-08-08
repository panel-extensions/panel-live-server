"""Headless-browser screenshot capture for rendered Panel snippets.

Wraps Playwright (Chromium) to load a ``/view`` page and capture a PNG so the
MCP ``screenshot`` tool can hand an LLM a picture of the *rendered* output —
the actual layout, fonts, and margins as a user would see them, not just the
source code.

A dashboard is often taller than the browser window, and often has tabs. Both
are invisible in a picture: a page cut in half looks exactly like a page that
ends, and a tab you did not open is absent in the same silent way an empty
chart is. So every capture also *reports* what it did not show — the tab
labels it found, and whether content continues past the fold (see
:class:`Capture`). Both facts are read off the loaded page for the cost of two
locator calls; neither clicks anything, and neither costs an extra image.

Acting on that report is the caller's choice, never this module's. Only the
caller knows the question being asked — "is the top chart blue?" needs one
picture, "review my dashboard" needs all of them — so ``full_page`` and
``select`` both default to the cheapest honest answer and the caller opts in.

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
from dataclasses import dataclass
from dataclasses import field

from panel_live_server.config import get_config

logger = logging.getLogger(__name__)


class PlaywrightUnavailableError(RuntimeError):
    """Raised when Playwright or its browser is not installed/launchable."""


class UnknownPageError(ValueError):
    """Raised when a caller names a page the dashboard does not have.

    Distinct from every other ``ValueError`` the capture path can raise (a
    malformed width, say) so the HTTP layer can answer "you asked for a page
    that isn't there" with a 400 without also blaming the caller for bugs.
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
        from playwright.sync_api import sync_playwright
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

# The page switchers of a multipage dashboard: Bokeh/Panel ``Tabs`` render
# ``.bk-tab``, Material-styled ones (panel-material-ui) render ``[role=tab]``.
# Only Playwright's selector engine finds these — Bokeh 3 mounts components into
# shadow roots, which ``document.querySelectorAll`` does not pierce.
#
# ``pn.Tabs`` is the only thing treated as pages. A ``Select`` or
# ``RadioButtonGroup`` wired to swap content is indistinguishable from a data
# filter without clicking through its options, and clicking runs the user's
# ``on_change`` handler — which may write a file, post a request, or drop a
# table. A tool called *screenshot* does not get to find that out by trying.
_PAGE_SELECTOR = ".bk-tab, [role=tab]"

#: Which of those tabs is showing. Bokeh marks the active tab with a class;
#: ARIA-styled tab strips use ``aria-selected``.
_ACTIVE_PAGE_SELECTOR = ".bk-tab.bk-active, [role=tab][aria-selected='true']"

#: The element whose scrolling reveals the rest of the page. Usually the
#: document, but Panel templates give ``body`` ``overflow: hidden`` and scroll
#: ``div.main`` instead — so ``window.scrollTo`` moves nothing and
#: ``documentElement.scrollHeight`` reports a single viewport. Picks whichever
#: scrollable element has the most content hidden below its own fold, and
#: descends into shadow roots for the same reason ``_PAGE_SELECTOR`` needs a
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

#: Longest single page label kept. Tabs are named, not paragraphed — anything
#: past this is a runaway string, and truncating it keeps the *other* labels.
_MAX_LABEL_CHARS = 80


@dataclass
class Capture:
    """One browser visit: the images taken, and an honest account of the rest.

    ``images`` holds ``(label, png)`` pairs. The label names the page and, when
    a page needed more than one, which screen of it — ``""`` for the ordinary
    single-image case, which is most of them.

    The remaining fields are the report. ``available_pages`` is every tab label
    found, ``captured_pages`` the ones actually visited; the difference is what
    the caller has not seen. ``total_tiles`` is how many screens of content the
    tallest captured page holds and ``captured_tiles`` how many came back;
    ``total_tiles > captured_tiles`` means the picture stops before the content
    does.
    """

    images: list[tuple[str, bytes]] = field(default_factory=list)
    available_pages: list[str] = field(default_factory=list)
    captured_pages: list[str] = field(default_factory=list)
    total_tiles: int = 1
    captured_tiles: int = 1

    @property
    def png(self) -> bytes | None:
        """The first PNG, for callers that only ever wanted one image."""
        return self.images[0][1] if self.images else None

    @property
    def unseen_pages(self) -> list[str]:
        """Page labels this capture found but did not return an image of."""
        return [label for label in self.available_pages if label not in self.captured_pages]


def encode_meta(capture: Capture) -> str:
    """Base64-encode *capture*'s report so it is safe to put in an HTTP header.

    Page labels are dropped from the end until the result fits
    ``_MAX_META_BYTES``; the tile counts are fixed-size and always survive.
    """
    labels = [label[:_MAX_LABEL_CHARS] for label in capture.available_pages]
    while True:
        payload = {
            "pages": labels,
            "captured": [label[:_MAX_LABEL_CHARS] for label in capture.captured_pages if label[:_MAX_LABEL_CHARS] in labels],
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
    capture.available_pages = _labels(meta, "pages")
    capture.captured_pages = _labels(meta, "captured")
    try:
        capture.total_tiles = max(1, int(meta.get("total_tiles", 1)))
        capture.captured_tiles = max(1, int(meta.get("captured_tiles", 1)))
    except (TypeError, ValueError):
        capture.total_tiles = capture.captured_tiles = 1
    return capture


def select_pages(labels: list[str], select: str, limit: int) -> list[int]:
    """Resolve *select* to the indices of ``labels`` to capture.

    An empty list means "capture whatever is on screen" — either the dashboard
    has no pages, or the caller did not ask for a specific one.

    Parameters
    ----------
    labels : list[str]
        Page labels discovered on the dashboard, in tab order.
    select : str
        ``""`` for the current page, ``"all"`` for every page, or a label or
        0-based index identifying one page.
    limit : int
        Cap on how many pages ``"all"`` expands to.

    Raises
    ------
    UnknownPageError
        If *select* names a page the dashboard does not have.
    """
    select = select.strip()
    if not labels or not select:
        return []
    if select.lower() == "all":
        return list(range(min(len(labels), limit)))
    if select.lstrip("-").isdigit() and 0 <= int(select) < len(labels):
        return [int(select)]
    for index, label in enumerate(labels):
        if label.lower() == select.lower():
            return [index]
    raise UnknownPageError(f"No page named {select!r}. This dashboard has: {', '.join(labels)}")


def tile_label(page: str, index: int, total: int) -> str:
    """Name one image: which page it is, and which screen of that page."""
    screen = f"screen {index + 1} of {total}" if total > 1 else ""
    if page and screen:
        return f"{page} — {screen}"
    return page or screen


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
                from playwright.async_api import async_playwright
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
        select: str = "",
        max_tiles: int = 4,
        max_pages: int = 12,
        console_sink: list[str] | None = None,
    ) -> Capture:
        """Load ``url`` in a fresh browser context and screenshot it.

        When ``console_sink`` is given, browser console messages and uncaught
        page errors are appended to it. Bokeh reports layout and tile failures
        only to the console, so without this a plot that dies client-side is
        indistinguishable in the PNG from one that simply has no data.
        """
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

            tabs = page.locator(_PAGE_SELECTOR)
            labels = [text.strip() for text in await tabs.all_text_contents()]
            wanted = select_pages(labels, select, max_pages)

            tiles = max_tiles if full_page else 1
            capture = Capture(available_pages=labels, total_tiles=1, captured_tiles=1)

            if not wanted:
                shots, total = await self._shoot(page, max_tiles=tiles)
                showing = await self._active_page(page, labels)
                capture.images = [(tile_label(showing, i, len(shots)), png) for i, png in enumerate(shots)]
                capture.captured_pages = [showing] if showing else []
                capture.total_tiles, capture.captured_tiles = total, len(shots)
                return capture

            for index in wanted:
                await tabs.nth(index).click()
                # A page switch re-lays-out and, for Bokeh, redraws from scratch.
                await page.wait_for_timeout(settle_ms)
                shots, total = await self._shoot(page, max_tiles=tiles)
                capture.images += [(tile_label(labels[index], i, len(shots)), png) for i, png in enumerate(shots)]
                capture.captured_pages.append(labels[index])
                # The report is about the worst page: if any of them continues
                # past its last image, the caller has not seen the whole thing.
                capture.total_tiles = max(capture.total_tiles, total)
                capture.captured_tiles = max(capture.captured_tiles, len(shots))
            return capture
        finally:
            await context.close()

    async def _active_page(self, page, labels: list[str]) -> str:
        """Best-effort read of which tab is showing. ``""`` when there are no tabs."""
        if not labels:
            return ""
        try:
            active = await page.locator(_ACTIVE_PAGE_SELECTOR).first.text_content(timeout=1000)
        except Exception:
            logger.debug("No active tab matched; assuming the first one.", exc_info=True)
            return labels[0]
        return (active or "").strip() or labels[0]

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
    select: str = "",
    max_tiles: int = 4,
    max_pages: int = 12,
    console_sink: list[str] | None = None,
) -> Capture:
    """Screenshot ``url`` using a shared headless browser.

    Both ``full_page`` and ``select`` default to the cheapest honest answer —
    one image of what is on screen — and the returned :class:`Capture` reports
    what that left out. Ask for more only when the question needs it.

    Parameters
    ----------
    full_page : bool, default False
        Capture the whole scrollable page as a run of viewport-sized tiles
        rather than the single visible screen.
    select : str, default ""
        Which page of a multipage dashboard to capture: ``""`` for whichever is
        showing, ``"all"`` for every one, or a tab label or 0-based index for
        one specific page.
    max_tiles : int, default 4
        Ceiling on how many tiles one ``full_page`` capture returns.
    max_pages : int, default 12
        Ceiling on how many pages ``select="all"`` expands to.
    console_sink : list[str], optional
        If given, browser console messages and uncaught page errors observed
        during the capture are appended to it.

    Returns
    -------
    Capture
        The images taken, plus the pages and tiles that were not.

    Raises
    ------
    PlaywrightUnavailableError
        If Playwright or a launchable browser is not available.
    UnknownPageError
        If ``select`` names a page the dashboard does not have.
    """
    return await _manager.capture(
        url,
        width=width,
        height=height,
        full_page=full_page,
        settle_ms=settle_ms,
        timeout_ms=timeout_ms,
        select=select,
        max_tiles=max_tiles,
        max_pages=max_pages,
        console_sink=console_sink,
    )


async def capture_png(url: str, **kwargs) -> bytes | None:
    """Capture a single PNG of ``url``. Thin wrapper over :func:`capture_pages`."""
    return (await capture_pages(url, **kwargs)).png
