"""Tests for the screenshot capture layer (issue #26).

The browser-facing parts are exercised end-to-end in ``test_integration``; what
is checked here is the logic that decides *what* to capture, which is where a
multipage dashboard silently turns into a one-page one.
"""

import pytest

from panel_live_server.screenshot import Capture
from panel_live_server.screenshot import decode_pages
from panel_live_server.screenshot import encode_pages
from panel_live_server.screenshot import select_pages

LABELS = ["Overview", "Sales", "Costs"]


class TestSelectPages:
    """Which pages of a tabbed dashboard a request resolves to."""

    def test_no_pages_captures_whatever_is_on_screen(self):
        """The overwhelmingly common case: an ordinary chart with no tabs at all."""
        assert select_pages([], "all", 12) == []

    def test_unset_captures_only_the_page_already_showing(self):
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
        with pytest.raises(ValueError, match="Overview, Sales, Costs"):
            select_pages(LABELS, "7", 12)

    def test_an_unknown_label_lists_the_ones_that_exist(self):
        with pytest.raises(ValueError, match="Overview, Sales, Costs"):
            select_pages(LABELS, "Profit", 12)


class TestPagesHeader:
    """The wire contract carrying page labels alongside a single PNG."""

    def test_round_trip(self):
        assert decode_pages(encode_pages(LABELS)) == LABELS

    def test_non_ascii_labels_survive(self):
        """Tab text is user-authored, so it is not safe to put in a header raw."""
        labels = ["Übersicht", "売上"]
        assert decode_pages(encode_pages(labels)) == labels

    def test_missing_header_is_not_an_error(self):
        assert decode_pages("") == []

    def test_junk_is_not_an_error(self):
        """A malformed header must not cost the caller the image it came with."""
        assert decode_pages("not-base64!!") == []


class TestCapture:
    def test_png_is_the_first_image(self):
        assert Capture(pages=[("Sales", b"A"), ("Costs", b"B")]).png == b"A"

    def test_png_is_none_when_nothing_was_captured(self):
        assert Capture().png is None
