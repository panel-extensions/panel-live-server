"""Browser-facing screenshot tests: the parts a pure function cannot check.

Everything here needs a real Chromium, because everything here is about what
Chromium does — which element a page actually scrolls, whether shadow-mounted
controls are reachable, whether a drag actually moves the mouse, whether the
tiles come back at viewport size. Run with::

    pytest tests/ui --ui

The fixtures are ``data:`` URLs rather than live Panel apps on purpose: the
behaviours under test are DOM behaviours, and a hand-written DOM makes the
awkward case (a template that hides ``body`` overflow and scrolls a nested
container instead, or a toolbar icon with no visible text) explicit rather
than incidental.
"""

import asyncio
import urllib.parse

import pytest

pytest.importorskip("playwright")

from panel_live_server import screenshot  # noqa: E402

pytestmark = pytest.mark.ui

WIDTH, HEIGHT = 600, 400

#: A short page: everything fits, nothing scrolls, nothing clickable.
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
#: capture after clicking "Sales" genuinely differs from the default capture.
#: Deliberately named and mounted exactly like the old dedicated tab selector
#: would have found, to prove a generic ``{"click": "<name>"}`` reaches the
#: same thing that hardcoding ``.bk-tab`` used to.
TABBED_PAGE = """
<body style="margin:0">
  <div class="bk-tabs">
    <div class="bk-tab bk-active" onclick="pick(0)">Overview</div>
    <div class="bk-tab" onclick="pick(1)">Sales</div>
    <div class="bk-tab" onclick="pick(2)">Costs</div>
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

#: A form: a labelled dropdown, a placeholder-only text input, an icon-only
#: button carrying a tooltip and no text (Bokeh's toolbar shape — a background
#: image, not visible text), and two same-labelled buttons to force ambiguity.
FORM_PAGE = """
<body style="margin:0">
  <label for="region">Region</label>
  <select id="region" onchange="document.title = this.value">
    <option>West</option>
    <option>East</option>
  </select>
  <input type="text" placeholder="Search" oninput="document.getElementById('echo').textContent = this.value">
  <div id="echo"></div>
  <div title="Box Zoom (either x, y or both dimensions)" onclick="document.getElementById('echo').textContent = 'zoomed'"
       style="width:20px;height:20px;background:#333"></div>
  <button id="save-a">Save</button>
  <button id="save-b">Save</button>
</body>
"""

#: A canvas that records where it was dragged, standing in for a Bokeh plot's
#: box-zoom gesture — the case nothing with a name can reach.
CANVAS_PAGE = """
<body style="margin:0">
  <canvas id="c" width="400" height="300" style="background:#222"></canvas>
  <div id="log"></div>
  <script>
    const c = document.getElementById('c');
    let start = null;
    c.addEventListener('mousedown', e => { start = [e.offsetX, e.offsetY]; });
    c.addEventListener('mouseup', e => {
      document.getElementById('log').textContent = [...start, e.offsetX, e.offsetY].join(',');
    });
  </script>
</body>
"""

#: Two steps whose *order* matters: the second only works once the first has
#: revealed it. Proves steps run in sequence, not independently.
SEQUENCE_PAGE = """
<body style="margin:0">
  <button onclick="document.getElementById('reveal').hidden = false">Reveal</button>
  <button id="reveal" hidden onclick="document.getElementById('log').textContent = 'clicked'">Confirm</button>
  <div id="log"></div>
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


async def _read(html: str, js: str, **kwargs):
    """Capture *html*, then evaluate *js* against the page it left open — used
    to check a side effect (what a click or drag actually did) that a PNG
    cannot assert on directly."""
    manager = screenshot._BrowserManager()
    browser = await manager._ensure_browser()
    context = await browser.new_context(viewport={"width": WIDTH, "height": HEIGHT})
    try:
        page = await context.new_page()
        await page.goto(data_url(html), wait_until="load")
        steps = screenshot.check_actions(kwargs.get("do"))
        for step in steps:
            await manager._perform(page, step)
        return await page.evaluate(js)
    finally:
        await context.close()
        if manager._browser is not None:
            await manager._browser.close()
        await manager._stop_playwright()


def read(html: str, js: str, **kwargs):
    return asyncio.run(_read(html, js, **kwargs))


class TestOrdinaryPage:
    def test_one_image_and_nothing_to_report(self):
        """The common case must cost no extra images and no extra words."""
        capture = shoot(SHORT_PAGE)
        assert len(capture.images) == 1
        assert capture.controls == []
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


class TestClickByName:
    """Generic name-based clicking replaces the old hardcoded ``.bk-tab`` probe."""

    def test_controls_are_reported_without_being_clicked(self):
        """The whole point of the report: naming what's there costs one locator
        call and runs none of the user's handlers."""
        capture = shoot(TABBED_PAGE)
        assert capture.controls == ["Overview", "Sales", "Costs"]
        assert len(capture.images) == 1

    def test_clicking_a_tab_by_name_captures_it(self):
        """No tab-specific code involved — this is the same generic text match
        that clicks a Button or a RadioButtonGroup option."""
        capture = shoot(TABBED_PAGE, do=[{"click": "Sales"}])
        assert len(capture.images) == 1

    def test_clicking_a_tab_actually_switches_the_panel(self):
        content = read(TABBED_PAGE, "document.getElementById('p1').hidden", do=[{"click": "Sales"}])
        assert content is False

    def test_a_typo_lists_the_real_control_names(self):
        with pytest.raises(screenshot.ActionError, match="Overview, Sales, Costs"):
            shoot(TABBED_PAGE, do=[{"click": "Saels"}])

    def test_an_ambiguous_name_reports_the_count_not_a_silent_guess(self):
        with pytest.raises(screenshot.ActionError, match="matches 2 elements"):
            shoot(FORM_PAGE, do=[{"click": "Save"}])

    def test_nth_picks_one_of_several_matches(self):
        """nth is positional over the matched set (both Save buttons), not over
        every element on the page — this pins that down against the wrong index."""
        focused = read(FORM_PAGE, "document.activeElement.id", do=[{"click": "Save", "nth": 1}])
        assert focused == "save-b"

    def test_an_icon_only_control_is_named_by_its_full_tooltip(self):
        """Real Bokeh toolbar buttons are plain <div>s (no <button> tag) with no
        visible text — only a title. Without [title] in _CONTROL_SELECTOR this
        control would be invisible to the report even though _locate could
        still reach it."""
        capture = shoot(FORM_PAGE)
        assert "Box Zoom (either x, y or both dimensions)" in capture.controls

    def test_clicking_by_a_short_name_reaches_a_control_with_a_longer_tooltip(self):
        """Bokeh's real Box Zoom tooltip is 'Box Zoom (either x, y or both
        dimensions)' — a caller should never have to quote that verbatim. This
        is the substring-title fallback, not the exact-match chain."""
        echoed = read(FORM_PAGE, "document.getElementById('echo').textContent", do=[{"click": "Box Zoom"}])
        assert echoed == "zoomed"


class TestFormSteps:
    def test_select_picks_an_option_by_its_label(self):
        """The <label for=...> and the <select> it names both carry text
        'Region' — this must resolve to the control, not report an ambiguity."""
        value = read(FORM_PAGE, "document.title", do=[{"select": "Region", "value": "East"}])
        assert value == "East"

    def test_fill_types_into_a_placeholder_only_input(self):
        echoed = read(FORM_PAGE, "document.getElementById('echo').textContent", do=[{"fill": "Search", "value": "acme"}])
        assert echoed == "acme"

    def test_a_missing_field_is_refused_before_any_picture(self):
        with pytest.raises(screenshot.ActionError, match="No element matches 'Nope'"):
            shoot(FORM_PAGE, do=[{"fill": "Nope", "value": "x"}])


class TestDrag:
    def test_drag_moves_the_mouse_from_start_to_end(self):
        log = read(CANVAS_PAGE, "document.getElementById('log').textContent", do=[{"drag": [50, 60, 200, 220]}])
        x0, y0, x1, y1 = (int(n) for n in log.split(","))
        # Canvas sits at the page origin, so page coordinates are canvas-local.
        assert (x0, y0) == (50, 60)
        assert (x1, y1) == (200, 220)


class TestStepOrdering:
    def test_steps_run_in_sequence_not_independently(self):
        """The second step targets an element the first step reveals; if steps
        ran out of order or in parallel this would fail with 'No element
        matches'."""
        log = read(SEQUENCE_PAGE, "document.getElementById('log').textContent", do=[{"click": "Reveal"}, {"click": "Confirm"}])
        assert log == "clicked"


class TestActionErrorsCostNoBrowser:
    def test_a_malformed_script_is_rejected_before_the_page_loads(self):
        """check_actions runs before _ensure_browser in capture() — this proves
        it end to end, not just as a unit test of the validator alone."""
        with pytest.raises(screenshot.ActionError, match="needs exactly four numbers"):
            shoot(SHORT_PAGE, do=[{"drag": [1, 2, 3]}])
