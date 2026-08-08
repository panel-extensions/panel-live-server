"""Browser-facing screenshot tests: the parts a pure function cannot check.

Everything here needs a real Chromium, because everything here is about what
Chromium does — which element a page actually scrolls, whether shadow-mounted
tabs are reachable, whether the tiles come back at viewport size. Run with::

    pytest tests/ui --ui

The fixtures are ``data:`` URLs rather than live Panel apps on purpose: the
behaviours under test are DOM behaviours, and a hand-written DOM makes the
awkward case (a template that hides ``body`` overflow and scrolls a nested
container instead) explicit rather than incidental.
"""

import asyncio
import urllib.parse

import pytest

pytest.importorskip("playwright")

from panel_live_server import screenshot  # noqa: E402

pytestmark = pytest.mark.ui

WIDTH, HEIGHT = 600, 400

#: A short page: everything fits, nothing scrolls, no tabs.
SHORT_PAGE = """
<body style="margin:0">
  <div class="bk" style="height:200px;background:#eee">just the one screen</div>
</body>
"""

#: The case ``documentElement.scrollHeight`` gets wrong. ``body`` cannot scroll;
#: the content lives in a nested ``overflow:auto`` container, exactly as
#: ``pn.template.FastListTemplate`` lays out its main area. 1600px of content in
#: a 400px viewport is four screens.
NESTED_SCROLLER_PAGE = """
<body style="margin:0;overflow:hidden;height:400px">
  <div class="main" style="height:400px;overflow:auto">
    <div class="bk" style="height:1600px;background:linear-gradient(#fff,#000)">tall</div>
  </div>
</body>
"""

#: Three Bokeh-style tabs. Clicking one swaps which panel is visible, so a
#: capture of tab 2 genuinely differs from a capture of tab 1.
TABBED_PAGE = """
<body style="margin:0">
  <div class="bk-tabs">
    <button class="bk-tab bk-active" onclick="pick(0)">Overview</button>
    <button class="bk-tab" onclick="pick(1)">Sales</button>
    <button class="bk-tab" onclick="pick(2)">Costs</button>
  </div>
  <div class="bk panel" id="p0">OVERVIEW</div>
  <div class="bk panel" id="p1" hidden>SALES</div>
  <div class="bk panel" id="p2" hidden>COSTS</div>
  <script>
    function pick(n) {
      for (let i = 0; i < 3; i++) {
        document.getElementById('p' + i).hidden = i !== n;
        document.querySelectorAll('.bk-tab')[i].classList.toggle('bk-active', i === n);
      }
    }
  </script>
</body>
"""


def data_url(html: str) -> str:
    return "data:text/html," + urllib.parse.quote(html)


async def _capture(html: str, **kwargs) -> screenshot.Capture:
    """Capture *html* with a browser of this test's own.

    Not the module-level ``_manager``: it keeps one browser alive across calls,
    and that browser belongs to the event loop that launched it. The server runs
    on a single loop so it gets the reuse for free, but a test that reaches for
    the shared browser from a second ``asyncio.run`` hangs waiting on a loop that
    is already closed.
    """
    manager = screenshot._BrowserManager()
    try:
        return await manager.capture(data_url(html), **kwargs)
    finally:
        if manager._browser is not None:
            await manager._browser.close()
        await manager._stop_playwright()


def shoot(html: str, **kwargs) -> screenshot.Capture:
    """Capture a fixture page, with the settle times wound down for the suite."""
    options = {"width": WIDTH, "height": HEIGHT, "full_page": False, "settle_ms": 50, "timeout_ms": 10000}
    return asyncio.run(_capture(html, **{**options, **kwargs}))


class TestOrdinaryPage:
    def test_one_image_and_nothing_to_report(self):
        """The common case must cost no extra images and no extra words."""
        capture = shoot(SHORT_PAGE)
        assert len(capture.images) == 1
        assert capture.available_pages == []
        assert capture.total_tiles == 1

    def test_asking_for_the_full_page_of_a_short_page_is_still_one_image(self):
        assert len(shoot(SHORT_PAGE, full_page=True).images) == 1


class TestNestedScroller:
    """A Panel template scrolls a container, not the window — the reason
    ``documentElement.scrollHeight`` alone reports a single viewport and
    ``window.scrollTo`` moves nothing."""

    def test_content_below_the_fold_is_counted_even_without_capturing_it(self):
        """The default capture stays one image, but must not claim to be whole."""
        capture = shoot(NESTED_SCROLLER_PAGE)
        assert len(capture.images) == 1
        assert capture.captured_tiles == 1
        assert capture.total_tiles == 4

    def test_full_page_returns_one_readable_tile_per_screen(self):
        capture = shoot(NESTED_SCROLLER_PAGE, full_page=True, max_tiles=8)
        assert len(capture.images) == 4
        assert (capture.total_tiles, capture.captured_tiles) == (4, 4)

    def test_the_tiles_are_actually_scrolled_not_duplicates(self):
        """Scrolling the *document* would leave four identical pictures — which
        is exactly the failure this test exists to catch."""
        tiles = [png for _, png in shoot(NESTED_SCROLLER_PAGE, full_page=True, max_tiles=8).images]
        assert len(set(tiles)) == 4

    def test_each_tile_is_viewport_sized_not_a_grown_window(self):
        """Growing the viewport instead of scrolling would let responsive content
        stretch, and would produce one image far taller than the window."""
        png = shoot(NESTED_SCROLLER_PAGE, full_page=True).png
        assert png is not None
        # PNG IHDR: width and height are big-endian uint32 at bytes 16..24.
        width = int.from_bytes(png[16:20], "big")
        height = int.from_bytes(png[20:24], "big")
        assert (width, height) == (WIDTH, HEIGHT)

    def test_the_tile_cap_truncates_and_says_so(self):
        capture = shoot(NESTED_SCROLLER_PAGE, full_page=True, max_tiles=2)
        assert len(capture.images) == 2
        assert (capture.total_tiles, capture.captured_tiles) == (4, 2)

    def test_tiles_are_labelled_with_their_position(self):
        labels = [label for label, _ in shoot(NESTED_SCROLLER_PAGE, full_page=True, max_tiles=2).images]
        assert labels == ["screen 1 of 2", "screen 2 of 2"]


class TestTabbedDashboard:
    def test_tabs_are_reported_without_being_clicked(self):
        """The whole point of the report: naming the pages costs one locator
        call and runs none of the user's handlers."""
        capture = shoot(TABBED_PAGE)
        assert capture.available_pages == ["Overview", "Sales", "Costs"]
        assert capture.captured_pages == ["Overview"]
        assert capture.unseen_pages == ["Sales", "Costs"]
        assert len(capture.images) == 1

    def test_naming_a_page_captures_that_page_only(self):
        capture = shoot(TABBED_PAGE, select="Sales")
        assert capture.captured_pages == ["Sales"]
        assert capture.unseen_pages == ["Overview", "Costs"]
        assert len(capture.images) == 1

    def test_all_captures_every_page_labelled(self):
        capture = shoot(TABBED_PAGE, select="all")
        assert [label for label, _ in capture.images] == ["Overview", "Sales", "Costs"]
        assert capture.unseen_pages == []

    def test_the_pages_are_genuinely_different_pictures(self):
        images = [png for _, png in shoot(TABBED_PAGE, select="all").images]
        assert len(set(images)) == 3

    def test_the_page_cap_truncates_and_leaves_the_rest_named(self):
        capture = shoot(TABBED_PAGE, select="all", max_pages=2)
        assert capture.captured_pages == ["Overview", "Sales"]
        assert capture.unseen_pages == ["Costs"]

    def test_an_unknown_page_names_the_ones_that_exist(self):
        with pytest.raises(screenshot.UnknownPageError, match="Overview, Sales, Costs"):
            shoot(TABBED_PAGE, select="Profit")
