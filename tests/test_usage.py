"""Tests for the per-session usage counters (issue #58)."""

import pytest

from panel_live_server import usage


@pytest.fixture(autouse=True)
def _clean_counters():
    """Counters are process-global, so a leftover count would leak between tests."""
    usage.reset()
    yield
    usage.reset()


class TestRecording:
    """The counters have to answer 'what did this session cost' honestly."""

    def test_starts_empty(self):
        snapshot = usage.snapshot()
        assert snapshot["total_chars"] == 0
        assert snapshot["total_calls"] == 0
        assert snapshot["by_tool"] == {}

    def test_accumulates_per_tool(self):
        usage.record("screenshot", 100)
        usage.record("screenshot", 150)
        usage.record("show", 50)

        snapshot = usage.snapshot()
        assert snapshot["by_tool"]["screenshot"] == {"chars": 250, "calls": 2}
        assert snapshot["by_tool"]["show"] == {"chars": 50, "calls": 1}
        assert snapshot["total_chars"] == 300
        assert snapshot["total_calls"] == 3

    def test_a_zero_char_call_still_counts_as_a_call(self):
        """This is the measurement, not an edge case.

        A promotion hands a finished visualization to the user while sending no
        code at all. If zero-character calls were dropped, the one number that
        demonstrates the saving would be invisible.
        """
        usage.record("promote", 0)
        usage.record("promote", 0)

        assert usage.snapshot()["by_tool"]["promote"] == {"chars": 0, "calls": 2}
        assert usage.snapshot()["total_calls"] == 2

    def test_a_draft_loop_shows_where_the_cost_went(self):
        """The shape the rework is meant to produce, asserted end to end.

        Three draft renders and two small edits, then a promotion that sends
        nothing. Before this work the same session would have paid a fourth full
        snippet on the final show.
        """
        snippet_len = 800
        for _ in range(3):
            usage.record("screenshot", snippet_len)
        usage.record("edit", 40)
        usage.record("edit", 25)
        usage.record("promote", 0)

        snapshot = usage.snapshot()
        assert snapshot["by_tool"]["promote"]["chars"] == 0
        assert snapshot["by_tool"]["edit"]["chars"] < snippet_len
        # The final hand-off cost nothing, where it used to cost a full resend.
        assert snapshot["total_chars"] == 3 * snippet_len + 65

    def test_negative_chars_are_ignored(self):
        usage.record("show", -10)
        assert usage.snapshot()["by_tool"]["show"] == {"chars": 0, "calls": 1}

    def test_reset_clears_everything_and_restarts_the_clock(self):
        usage.record("show", 10)
        before = usage.snapshot()["since"]

        usage.reset()

        snapshot = usage.snapshot()
        assert snapshot["total_calls"] == 0
        assert snapshot["since"] >= before

    def test_snapshot_is_a_copy(self):
        """Callers serialize this into a response; mutating it must not corrupt state."""
        usage.record("show", 10)
        snapshot = usage.snapshot()
        snapshot["by_tool"]["show"]["chars"] = 999
        snapshot["total_chars"] = 999

        assert usage.snapshot()["by_tool"]["show"]["chars"] == 10
        assert usage.snapshot()["total_chars"] == 10
