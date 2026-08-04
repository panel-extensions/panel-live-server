"""Tests for the diagnostics buffer that carries render output back to callers."""

import sys

from panel_live_server import diagnostics


class TestRecordAndPop:
    """Output is stored per snippet and consumed once."""

    def setup_method(self) -> None:
        diagnostics._store.clear()

    def test_appends_across_calls(self) -> None:
        """A partial write before an exception must not be lost."""
        diagnostics.record("a", "first\n")
        diagnostics.record("a", "second\n")
        assert diagnostics.pop("a") == "first\nsecond\n"

    def test_pop_consumes(self) -> None:
        diagnostics.record("a", "once")
        assert diagnostics.pop("a") == "once"
        assert diagnostics.pop("a") == ""

    def test_blank_and_idless_writes_are_ignored(self) -> None:
        """Whitespace-only output is noise, and an id-less record has no owner."""
        diagnostics.record("a", "   \n\t")
        diagnostics.record("", "orphan")
        assert diagnostics.pop("a") == ""
        assert diagnostics.pop("") == ""

    def test_oldest_entries_are_evicted(self) -> None:
        """The store is bounded, so a long-running server cannot grow forever."""
        for i in range(diagnostics.MAX_ENTRIES + 5):
            diagnostics.record(f"s{i}", "x")
        assert len(diagnostics._store) == diagnostics.MAX_ENTRIES
        assert diagnostics.pop("s0") == ""
        assert diagnostics.pop(f"s{diagnostics.MAX_ENTRIES + 4}") == "x"


class TestTruncate:
    """Clipping keeps the end, where the useful part is."""

    def test_short_text_is_untouched(self) -> None:
        assert diagnostics.truncate("short", limit=100) == "short"

    def test_long_text_keeps_the_tail(self) -> None:
        text = "".join(str(i % 10) for i in range(500))
        clipped = diagnostics.truncate(text, limit=50)
        assert clipped.endswith(text[-50:])
        assert "earlier characters omitted" in clipped


class TestCollapseRepeats:
    """One fault repeated per tile must not crowd out everything else."""

    def test_consecutive_duplicates_are_counted(self) -> None:
        assert diagnostics.collapse_repeats(["a", "a", "a", "b", "a"]) == ["a  (x3)", "b", "a"]

    def test_empty_list(self) -> None:
        assert diagnostics.collapse_repeats([]) == []


class TestBuildAndTransport:
    """The payload survives the trip through an HTTP header."""

    def test_build_includes_both_streams(self) -> None:
        payload = diagnostics.build("printed\n", ["[error] boom"] * 3)
        assert payload["python"] == "printed"
        assert "(x3)" in payload["console"]

    def test_build_is_empty_when_nothing_happened(self) -> None:
        assert diagnostics.build("", []) == {}
        assert diagnostics.build("   ", None) == {}

    def test_encode_decode_round_trip(self) -> None:
        payload = diagnostics.build("value: 42\n", ["[warning] hm"])
        assert diagnostics.decode(diagnostics.encode(payload)) == payload

    def test_decode_tolerates_junk(self) -> None:
        """A malformed header should degrade to no diagnostics, not an exception."""
        assert diagnostics.decode("") == {}
        assert diagnostics.decode("!!!not base64!!!") == {}

    def test_render_labels_each_stream(self) -> None:
        text = diagnostics.render(diagnostics.build("out\n", ["[error] bad"]))
        assert "stdout/stderr from the snippet:" in text
        assert "browser console:" in text

    def test_render_of_nothing_is_empty(self) -> None:
        assert diagnostics.render({}) == ""


class TestConsoleLineCap:
    """The console sink is bounded by MAX_CONSOLE_LINES."""

    def test_cap_is_exported_for_screenshot(self) -> None:
        # screenshot.py imports this to bound its sink; keep it importable.
        assert isinstance(diagnostics.MAX_CONSOLE_LINES, int)
        assert diagnostics.MAX_CONSOLE_LINES > 0


class TestNoModuleLeak:
    """Guard against evaluation modules lingering in sys.modules."""

    def test_no_pls_eval_modules_left_behind(self) -> None:
        assert not [name for name in sys.modules if name.startswith("pls_eval_")]
