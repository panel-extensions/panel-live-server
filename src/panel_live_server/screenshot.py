"""Headless-browser screenshot capture for rendered Panel snippets.

Wraps Playwright (Chromium) to load a ``/view`` page and capture a PNG so the
MCP ``screenshot`` tool can hand an LLM a picture of the *rendered* output —
the actual layout, fonts, and margins as a user would see them, not just the
source code.

Playwright is a **required** dependency (included in the base install). Import /
launch failures are surfaced as :class:`PlaywrightUnavailableError` with an
install hint so callers can degrade gracefully instead of crashing.
"""

import asyncio
import base64
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from dataclasses import field

from panel_live_server.config import get_config

logger = logging.getLogger(__name__)


class PlaywrightUnavailableError(RuntimeError):
    """Raised when Playwright or its browser is not installed/launchable."""


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
_PAGE_SELECTOR = ".bk-tab, [role=tab]"

#: Tallest extent of the page, in CSS px, including content that only scrolls
#: inside a nested container. Panel templates give ``body`` ``overflow: hidden``
#: and scroll ``div.main`` instead, so ``documentElement.scrollHeight`` alone
#: reports one viewport and Playwright's ``full_page`` capture stops at the fold.
#: Descends into shadow roots for the same reason ``_PAGE_SELECTOR`` needs a
#: Playwright locator.
_CONTENT_EXTENT_JS = """() => {
    let extent = document.documentElement.scrollHeight;
    const visit = (root) => {
        for (const el of root.querySelectorAll('*')) {
            const overflowY = getComputedStyle(el).overflowY;
            if ((overflowY === 'auto' || overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 4) {
                extent = Math.max(extent, el.getBoundingClientRect().top + window.scrollY + el.scrollHeight);
            }
            if (el.shadowRoot) visit(el.shadowRoot);
        }
    };
    visit(document);
    return Math.ceil(extent);
}"""

#: HTTP header carrying the base64-encoded page labels alongside a single PNG,
#: so a caller that captured one page still learns the others exist. A wire
#: contract between ``endpoints`` and ``client``, like ``diagnostics.HEADER``.
PAGES_HEADER = "X-PLS-Pages"


@dataclass
class Capture:
    """One browser visit: the PNGs taken, and every page that could be taken.

    ``pages`` holds ``(label, png)`` pairs — the label is the page's tab text,
    or ``""`` for a dashboard that has no pages (the overwhelmingly common
    case). ``available`` lists every page label found, whether captured or not.
    """

    pages: list[tuple[str, bytes]] = field(default_factory=list)
    available: list[str] = field(default_factory=list)

    @property
    def png(self) -> bytes | None:
        """The first PNG, for callers that only ever wanted one image."""
        return self.pages[0][1] if self.pages else None


def encode_pages(labels: list[str]) -> str:
    """Base64-encode *labels* so they are safe to put in an HTTP header."""
    return base64.b64encode(json.dumps(labels).encode("utf-8")).decode("ascii")


def decode_pages(raw: str) -> list[str]:
    """Inverse of :func:`encode_pages`. Returns ``[]`` rather than raising on junk."""
    if not raw:
        return []
    try:
        decoded = json.loads(base64.b64decode(raw.encode("ascii")).decode("utf-8"))
    except Exception:
        logger.debug("Could not decode %s header", PAGES_HEADER, exc_info=True)
        return []
    return [str(label) for label in decoded] if isinstance(decoded, list) else []


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
    ValueError
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
    raise ValueError(f"No page named {select!r}. This dashboard has: {', '.join(labels)}")


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
        max_height: int = 10000,
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

            async def shoot() -> bytes:
                return await self._shoot(page, width=width, base_height=height, full_page=full_page, settle_ms=settle_ms, max_height=max_height)

            if not wanted:
                return Capture(pages=[("", await shoot())], available=labels)

            shots: list[tuple[str, bytes]] = []
            for index in wanted:
                await tabs.nth(index).click()
                # A tab switch re-lays-out and, for Bokeh, redraws from scratch.
                await page.wait_for_timeout(settle_ms)
                shots.append((labels[index], await shoot()))
            return Capture(pages=shots, available=labels)
        finally:
            await context.close()

    async def _shoot(self, page, *, width: int, base_height: int, full_page: bool, settle_ms: int, max_height: int) -> bytes:
        """Screenshot the loaded *page*, growing the viewport when asked for the whole thing.

        ``full_page`` on its own only unrolls the *document* scroll. A Panel
        template scrolls a nested container instead, so the viewport is grown to
        the measured content extent and the layout is allowed to reflow into it —
        which is what makes the rest of the dashboard exist to be captured at all.
        Repeated once, because reflowing taller content can reveal a little more.
        """
        if not full_page:
            return await page.screenshot(type="png")

        try:
            for _ in range(2):
                extent = min(int(await page.evaluate(_CONTENT_EXTENT_JS)), max_height)
                current = (page.viewport_size or {}).get("height", base_height)
                if extent <= current:
                    break
                await page.set_viewport_size({"width": width, "height": extent})
                await page.wait_for_timeout(settle_ms)
            return await page.screenshot(type="png", full_page=True)
        finally:
            # Leave the next page of a multipage capture measuring from the same
            # starting viewport this one did.
            await page.set_viewport_size({"width": width, "height": base_height})

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
    full_page: bool = True,
    settle_ms: int = 1200,
    timeout_ms: int = 30000,
    select: str = "",
    max_height: int = 10000,
    max_pages: int = 12,
    console_sink: list[str] | None = None,
) -> Capture:
    """Screenshot ``url`` using a shared headless browser.

    Parameters
    ----------
    full_page : bool, default True
        Capture everything the page can scroll to, not just the viewport.
    select : str, default ""
        Which page of a multipage dashboard to capture: ``""`` for whichever is
        showing, ``"all"`` for every one, or a tab label or 0-based index.
    max_height : int, default 10000
        Ceiling (px) on how tall a ``full_page`` capture is allowed to grow.
    max_pages : int, default 12
        Ceiling on how many pages ``select="all"`` expands to.
    console_sink : list[str], optional
        If given, browser console messages and uncaught page errors observed
        during the capture are appended to it.

    Returns
    -------
    Capture
        The PNGs taken and the labels of every page that could be taken.

    Raises
    ------
    PlaywrightUnavailableError
        If Playwright or a launchable browser is not available.
    ValueError
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
        max_height=max_height,
        max_pages=max_pages,
        console_sink=console_sink,
    )


async def capture_png(url: str, **kwargs) -> bytes | None:
    """Capture a single PNG of ``url``. Thin wrapper over :func:`capture_pages`."""
    return (await capture_pages(url, **kwargs)).png
