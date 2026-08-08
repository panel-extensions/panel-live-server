"""Tests for the screenshot capture layer (issue #26).

Two things decide whether an agent reports on a whole dashboard or a third of
one: which pages a request resolves to, and whether the reply admits to what it
left out. Both are pure functions and both are checked here.

The browser half — finding the element a Panel template actually scrolls, and
tiling it — needs a real Chromium, so it lives in ``tests/ui/test_screenshot_ui``.
"""

import pytest

from panel_live_server.screenshot import _MAX_META_BYTES
from panel_live_server.screenshot import Capture
from panel_live_server.screenshot import UnknownPageError
from panel_live_server.screenshot import apply_meta
from panel_live_server.screenshot import decode_meta
from panel_live_server.screenshot import encode_meta
from panel_live_server.screenshot import select_pages
from panel_live_server.screenshot import tile_label

LABELS = ["Overview", "Sales", "Costs"]


class TestSelectPages:
    """Which pages of a tabbed dashboard a request resolves to."""

    def test_no_pages_captures_whatever_is_on_screen(self):
        """The overwhelmingly common case: an ordinary chart with no tabs at all."""
        assert select_pages([], "all", 12) == []

    def test_unset_captures_only_the_page_already_showing(self):
        """The default. Everything else is the caller opting in to more."""
        assert select_pages(LABELS, "", 12) == []

    def test_all_expands_to_every_page(self):
        assert select_pages(LABELS, "all", 12) == [0, 1, 2]

    def test_all_is_capped_so_a_huge_dashboard_cannot_flood_the_reply(self):
        assert select_pages(LABELS, "all", 2) == [0, 1]

    def test_a_label_selects_its_page(self):
        assert select_pages(LABELS, "Sales", 12) == [1]

    def test_label_matching_ignores_case(self):
        """The label is read off a rendered tab, so its exact casing is a detail."""
        assert select_pages(LABELS, "sales", 12) == [1]

    def test_an_index_selects_its_page(self):
        assert select_pages(LABELS, "2", 12) == [2]

    def test_an_out_of_range_index_is_reported_not_clamped(self):
        """Silently capturing a different page than asked for is the worse failure."""
        with pytest.raises(UnknownPageError, match="Overview, Sales, Costs"):
            select_pages(LABELS, "7", 12)

    def test_an_unknown_label_lists_the_ones_that_exist(self):
        with pytest.raises(UnknownPageError, match="Overview, Sales, Costs"):
            select_pages(LABELS, "Profit", 12)

    def test_unknown_page_is_distinguishable_from_any_other_bad_value(self):
        """The HTTP layer answers 400 for this and 500 for our own bugs, so the
        two must not arrive as the same bare ValueError."""
        assert issubclass(UnknownPageError, ValueError)


class TestTileLabel:
    """How each image announces itself when more than one comes back."""

    def test_a_lone_image_of_a_pageless_app_needs_no_label(self):
        assert tile_label("", 0, 1) == ""

    def test_a_lone_image_of_a_page_is_named_after_it(self):
        assert tile_label("Sales", 0, 1) == "Sales"

    def test_tiles_are_numbered_from_one(self):
        """The model reads these; 'screen 0 of 3' would be nonsense to it."""
        assert tile_label("", 0, 3) == "screen 1 of 3"

    def test_a_tiled_page_names_both(self):
        assert tile_label("Sales", 1, 3) == "Sales — screen 2 of 3"


class TestCaptureMetaHeader:
    """The wire contract carrying the report alongside the image(s)."""

    def _capture(self, **kwargs) -> Capture:
        return Capture(available_pages=LABELS, captured_pages=["Sales"], **kwargs)

    def test_round_trip(self):
        restored = apply_meta(Capture(), decode_meta(encode_meta(self._capture(total_tiles=4, captured_tiles=1))))
        assert restored.available_pages == LABELS
        assert restored.captured_pages == ["Sales"]
        assert (restored.total_tiles, restored.captured_tiles) == (4, 1)

    def test_non_ascii_labels_survive(self):
        """Tab text is user-authored, so it is not safe to put in a header raw."""
        labels = ["Übersicht", "売上"]
        restored = apply_meta(Capture(), decode_meta(encode_meta(Capture(available_pages=labels))))
        assert restored.available_pages == labels

    def test_missing_header_is_not_an_error(self):
        assert decode_meta("") == {}

    def test_junk_is_not_an_error(self):
        """A malformed header must not cost the caller the image it came with."""
        assert decode_meta("not-base64!!") == {}

    def test_a_junk_header_leaves_a_usable_capture(self):
        restored = apply_meta(Capture(images=[("", b"PNG")]), decode_meta("not-base64!!"))
        assert restored.png == b"PNG"
        assert restored.available_pages == []
        assert (restored.total_tiles, restored.captured_tiles) == (1, 1)

    def test_a_non_numeric_tile_count_does_not_raise(self):
        assert apply_meta(Capture(), {"total_tiles": "lots"}).total_tiles == 1

    def test_a_runaway_label_cannot_blow_the_header(self):
        """Tab text is unbounded user input going into an HTTP header, where too
        long means the whole request fails — so labels are what gets dropped."""
        encoded = encode_meta(Capture(available_pages=["x" * 50_000] * 20))
        assert len(encoded) <= _MAX_META_BYTES

    def test_page_names_are_dropped_before_the_tile_count_is(self):
        """Losing a page name costs the caller a name it can still ask about;
        losing the tile count costs it the knowledge that anything is missing."""
        restored = apply_meta(Capture(), decode_meta(encode_meta(Capture(available_pages=["y" * 70] * 200, total_tiles=5))))
        assert restored.total_tiles == 5
        assert len(restored.available_pages) < 200


class TestCapture:
    def test_png_is_the_first_image(self):
        assert Capture(images=[("Sales", b"A"), ("Costs", b"B")]).png == b"A"

    def test_png_is_none_when_nothing_was_captured(self):
        assert Capture().png is None

    def test_unseen_pages_are_the_ones_not_visited(self):
        assert Capture(available_pages=LABELS, captured_pages=["Sales"]).unseen_pages == ["Overview", "Costs"]

    def test_nothing_is_unseen_once_every_page_is_captured(self):
        assert Capture(available_pages=LABELS, captured_pages=LABELS).unseen_pages == []

    def test_a_chart_with_no_pages_has_nothing_unseen(self):
        assert Capture(images=[("", b"PNG")]).unseen_pages == []
