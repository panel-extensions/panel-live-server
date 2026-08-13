"""Tests for the screenshot capture layer (issue #26).

Three things decide what a caller gets back: whether a ``do`` script is even
well-formed, whether the reply admits to what it left out, and how a tile
announces itself. All three are pure functions and all three are checked here.

The browser half — finding a named element on a real page, clicking it,
dragging on a canvas, and tiling a tall page — needs a real Chromium, so it
lives in ``tests/ui/test_screenshot_ui``.
"""

import pytest

from panel_live_server.screenshot import _MAX_META_BYTES
from panel_live_server.screenshot import ActionError
from panel_live_server.screenshot import Capture
from panel_live_server.screenshot import apply_meta
from panel_live_server.screenshot import check_actions
from panel_live_server.screenshot import decode_meta
from panel_live_server.screenshot import encode_meta
from panel_live_server.screenshot import tile_label

CONTROLS = ["Overview", "Sales", "Costs"]


class TestCheckActions:
    """Whether a ``do`` script is well-formed, checked before any browser is involved."""

    def test_none_is_no_steps(self):
        assert check_actions(None) == []

    def test_empty_list_is_no_steps(self):
        assert check_actions([]) == []

    def test_a_well_formed_click_passes_through(self):
        assert check_actions([{"click": "Reports"}]) == [{"click": "Reports"}]

    def test_a_well_formed_click_with_nth_passes_through(self):
        assert check_actions([{"click": "Reports", "nth": 1}]) == [{"click": "Reports", "nth": 1}]

    def test_select_needs_a_value(self):
        with pytest.raises(ActionError, match="needs a string 'value'"):
            check_actions([{"select": "Region"}])

    def test_a_well_formed_select_passes(self):
        step = {"select": "Region", "value": "West"}
        assert check_actions([step]) == [step]

    def test_fill_needs_a_value(self):
        with pytest.raises(ActionError, match="needs a string 'value'"):
            check_actions([{"fill": "Search"}])

    def test_a_well_formed_fill_passes(self):
        step = {"fill": "Search", "value": "acme"}
        assert check_actions([step]) == [step]

    def test_a_well_formed_key_passes(self):
        assert check_actions([{"key": "Enter"}]) == [{"key": "Enter"}]

    def test_an_empty_key_is_rejected(self):
        with pytest.raises(ActionError, match="needs a key name"):
            check_actions([{"key": ""}])

    def test_a_well_formed_drag_passes(self):
        step = {"drag": [10, 20, 300, 400]}
        assert check_actions([step]) == [step]

    def test_drag_needs_exactly_four_numbers(self):
        with pytest.raises(ActionError, match="needs exactly four numbers"):
            check_actions([{"drag": [10, 20, 300]}])

    def test_drag_rejects_non_numeric_coordinates(self):
        with pytest.raises(ActionError, match="needs exactly four numbers"):
            check_actions([{"drag": [10, 20, 300, "x"]}])

    def test_drag_rejects_bools_as_coordinates(self):
        """A bool is an int subclass; letting one through would silently drag from 1/0."""
        with pytest.raises(ActionError, match="needs exactly four numbers"):
            check_actions([{"drag": [True, 20, 300, 400]}])

    def test_a_well_formed_wait_passes(self):
        assert check_actions([{"wait": 500}]) == [{"wait": 500}]

    def test_wait_rejects_a_negative_number(self):
        with pytest.raises(ActionError, match="non-negative"):
            check_actions([{"wait": -1}])

    def test_wait_rejects_a_bool(self):
        with pytest.raises(ActionError, match="non-negative"):
            check_actions([{"wait": True}])

    def test_an_unknown_verb_names_the_valid_shapes(self):
        with pytest.raises(ActionError, match="Valid steps are"):
            check_actions([{"scroll": "down"}])

    def test_a_step_with_no_verb_at_all_is_rejected(self):
        with pytest.raises(ActionError, match="names no action"):
            check_actions([{"nth": 1}])

    def test_a_step_naming_two_actions_is_rejected(self):
        with pytest.raises(ActionError, match="names 2 actions"):
            check_actions([{"click": "Go", "fill": "Search", "value": "x"}])

    def test_a_step_that_is_not_an_object_is_rejected(self):
        with pytest.raises(ActionError, match="must be an object"):
            check_actions(["click Reports"])

    def test_do_itself_must_be_a_list(self):
        with pytest.raises(ActionError, match="must be a list of steps"):
            check_actions("click Reports")

    def test_too_many_steps_are_rejected(self):
        with pytest.raises(ActionError, match="at most 2"):
            check_actions([{"click": "A"}, {"click": "B"}, {"click": "C"}], limit=2)

    def test_click_rejects_a_non_integer_nth(self):
        with pytest.raises(ActionError, match="non-integer 'nth'"):
            check_actions([{"click": "Reports", "nth": "1"}])

    def test_click_rejects_an_empty_name(self):
        with pytest.raises(ActionError, match="non-empty name"):
            check_actions([{"click": "  "}])

    def test_action_error_is_a_value_error(self):
        """The HTTP layer answers 400 for this and 500 for our own bugs, so the
        two must not arrive as the same bare ValueError."""
        assert issubclass(ActionError, ValueError)


class TestTileLabel:
    """How each image announces itself when a page needed more than one."""

    def test_a_lone_image_needs_no_label(self):
        assert tile_label(0, 1) == ""

    def test_tiles_are_numbered_from_one(self):
        """The model reads these; 'screen 0 of 3' would be nonsense to it."""
        assert tile_label(0, 3) == "screen 1 of 3"

    def test_a_later_tile_says_which_one_it_is(self):
        assert tile_label(1, 3) == "screen 2 of 3"


class TestCaptureMetaHeader:
    """The wire contract carrying the report alongside the image(s)."""

    def _capture(self, **kwargs) -> Capture:
        return Capture(controls=CONTROLS, **kwargs)

    def test_round_trip(self):
        restored = apply_meta(Capture(), decode_meta(encode_meta(self._capture(total_tiles=4, captured_tiles=1))))
        assert restored.controls == CONTROLS
        assert (restored.total_tiles, restored.captured_tiles) == (4, 1)

    def test_non_ascii_labels_survive(self):
        """Control text is user-authored, so it is not safe to put in a header raw."""
        labels = ["Übersicht", "売上"]
        restored = apply_meta(Capture(), decode_meta(encode_meta(Capture(controls=labels))))
        assert restored.controls == labels

    def test_missing_header_is_not_an_error(self):
        assert decode_meta("") == {}

    def test_junk_is_not_an_error(self):
        """A malformed header must not cost the caller the image it came with."""
        assert decode_meta("not-base64!!") == {}

    def test_a_junk_header_leaves_a_usable_capture(self):
        restored = apply_meta(Capture(images=[("", b"PNG")]), decode_meta("not-base64!!"))
        assert restored.png == b"PNG"
        assert restored.controls == []
        assert (restored.total_tiles, restored.captured_tiles) == (1, 1)

    def test_a_non_numeric_tile_count_does_not_raise(self):
        assert apply_meta(Capture(), {"total_tiles": "lots"}).total_tiles == 1

    def test_a_runaway_label_cannot_blow_the_header(self):
        """Control text is unbounded user input going into an HTTP header, where
        too long means the whole request fails — so labels are what gets dropped."""
        encoded = encode_meta(Capture(controls=["x" * 50_000] * 20))
        assert len(encoded) <= _MAX_META_BYTES

    def test_control_names_are_dropped_before_the_tile_count_is(self):
        """Losing a control name costs the caller a name it can still ask about;
        losing the tile count costs it the knowledge that anything is missing."""
        restored = apply_meta(Capture(), decode_meta(encode_meta(Capture(controls=["y" * 70] * 200, total_tiles=5))))
        assert restored.total_tiles == 5
        assert len(restored.controls) < 200


class TestCapture:
    def test_png_is_the_first_image(self):
        assert Capture(images=[("screen 1 of 2", b"A"), ("screen 2 of 2", b"B")]).png == b"A"

    def test_png_is_none_when_nothing_was_captured(self):
        assert Capture().png is None
