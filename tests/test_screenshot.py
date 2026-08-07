"""Tests for the screenshot capture layer (issue #26).

The browser-facing parts are exercised end-to-end in ``test_integration``; what
is checked here is the logic that decides *what* to capture, which is where a
multipage dashboard silently turns into a one-page one.
"""

import pytest

from panel_live_server.screenshot import _NAV_DISTINCTNESS_THRESHOLD
from panel_live_server.screenshot import Capture
from panel_live_server.screenshot import _text_distinctness
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


class TestTextDistinctness:
    """Deciding whether a selection widget's options are different pages or the
    same page with a filter/theme changed — the fallback for dashboards that
    hand-build navigation instead of using pn.Tabs (issue #26 follow-up).

    Fixture text below is drawn from live probes of a real 4-page dashboard
    built with RadioButtonGroup (scored ~0.66-0.71) and two decoys that must
    NOT trigger detection: a Light/Dark theme toggle over static content
    (scored 0.0), and a Region Select that swaps a Tabulator's numbers while
    keeping identical headings/structure (scored 0.67 raw, 0.0 once digit runs
    are normalized — the reason normalization exists at all).
    """

    def test_identical_options_are_not_distinct(self):
        assert _text_distinctness(["Same text every time", "Same text every time"]) == 0.0

    def test_completely_different_options_are_distinct(self):
        score = _text_distinctness(["Financial Overview revenue expenses", "Sales Performance region growth"])
        assert score > _NAV_DISTINCTNESS_THRESHOLD

    def test_a_single_sample_is_not_distinct(self):
        """Nothing to compare against — must not divide by zero or false-positive."""
        assert _text_distinctness(["only one sample"]) == 0.0

    def test_no_samples_is_not_distinct(self):
        assert _text_distinctness([]) == 0.0

    def test_real_four_page_dashboard_clears_the_threshold(self):
        """The actual case this exists for: RadioButtonGroup-based page routing."""
        samples = [
            "Toggle the Sidebar Business Analytics Dashboard Financial Overview revenue vs expenses chart",
            "Toggle the Sidebar Business Analytics Dashboard Sales Performance region growth North South East West Central",
            "Toggle the Sidebar Business Analytics Dashboard Monthly Reports Q1 Summary Q2 Summary Q3 Summary revenue increased",
            "Toggle the Sidebar Business Analytics Dashboard Key Performance Indicators Revenue Target Expense Limit On Track At Risk",
        ]
        assert _text_distinctness(samples) >= _NAV_DISTINCTNESS_THRESHOLD

    def test_theme_toggle_over_static_content_is_not_distinct(self):
        """A Light/Dark toggle changes styling, not text — must not be mistaken for pages."""
        samples = [
            "LightDark Static Report This text never changes no matter which theme is picked",
            "LightDark Static Report This text never changes no matter which theme is picked",
        ]
        assert _text_distinctness(samples) < _NAV_DISTINCTNESS_THRESHOLD

    def test_data_filter_that_only_changes_numbers_is_not_distinct(self):
        """Same headings/structure, different numbers — a filter, not a page switch.

        Without digit normalization this scores as distinct as real navigation
        (confirmed live: 0.67, same order of magnitude as the real dashboard's
        0.66-0.71) — this test is what would catch that regression.
        """
        samples = [
            "Region North South East Sales Report month sales 0 Jan 100 1 Feb 120 2 Mar 90",
            "Region North South East Sales Report month sales 0 Jan 80 1 Feb 95 2 Mar 70",
            "Region North South East Sales Report month sales 0 Jan 150 1 Feb 140 2 Mar 160",
        ]
        assert _text_distinctness(samples) < _NAV_DISTINCTNESS_THRESHOLD

    def test_a_filter_that_also_changes_a_little_text_stays_below_threshold(self):
        """Real filters often vary a label too ('Showing: North') — must not tip the balance."""
        samples = [
            "Region North South East Showing North sales report",
            "Region North South East Showing South sales report",
        ]
        assert _text_distinctness(samples) < _NAV_DISTINCTNESS_THRESHOLD
