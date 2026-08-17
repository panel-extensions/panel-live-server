"""Tests for user-configurable MCP prompts (issue #50)."""

import json

import pytest

from panel_live_server import prompts
from panel_live_server.prompts import SCREENSHOT
from panel_live_server.prompts import known_sections
from panel_live_server.prompts import render_instructions
from panel_live_server.prompts import render_prompt

_PROMPTS_FILE_ENV = "PANEL_LIVE_SERVER_PROMPTS_FILE"


@pytest.fixture(autouse=True)
def _clean_prompt_env(monkeypatch):
    """A developer's own --prompts file must not leak into these assertions."""
    monkeypatch.delenv(_PROMPTS_FILE_ENV, raising=False)


@pytest.fixture
def write_prompts(monkeypatch, tmp_path):
    """Write a prompts file and point the server at it, as `--prompts` would."""

    def _write(payload):
        path = tmp_path / "my-prompts.json"
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
        monkeypatch.setenv(_PROMPTS_FILE_ENV, str(path))
        return path

    return _write


class TestDefaults:
    """With no --prompts file, the shipped prompt is used unchanged."""

    def test_every_section_is_overridable(self):
        """Whatever is a block is exactly what a --prompts file may set."""
        assert known_sections() == [
            "intro",
            "workflow",
            "file_policy",
            "library_selection",
            "extensions",
            "security",
            "rendering",
            "output",
            "errors",
            "screenshot",
        ]

    def test_default_render_contains_every_section(self):
        text = render_instructions()
        assert "WORKFLOW:" in text
        assert "LIBRARY SELECTION:" in text
        assert "RENDERING" in text
        assert "OUTPUT" in text
        assert "ERRORS" in text

    def test_default_recommends_holoviz(self):
        assert "PRIMARILY write visualizations with HoloViz packages" in render_instructions()


class TestScreenshotPrompt:
    """The screenshot tool's prompt is one configurable section covering both call forms."""

    def test_draft_review_default(self):
        assert "REVIEW THIS DRAFT" in render_prompt(SCREENSHOT, "draft_review")

    def test_shown_image_default(self):
        assert "IMAGE QUALITY CHECK" in render_prompt(SCREENSHOT, "shown_image")

    def test_the_two_call_forms_keep_distinct_defaults(self):
        """Reviewing your own draft and reading a shown image need different advice."""
        assert "IMAGE QUALITY CHECK" not in render_prompt(SCREENSHOT, "draft_review")
        assert "REVIEW THIS DRAFT" not in render_prompt(SCREENSHOT, "shown_image")

    def test_one_key_reaches_both_call_forms(self, write_prompts):
        write_prompts({"screenshot": "Also check the axis labels are readable."})

        for block, default in [("draft_review", "REVIEW THIS DRAFT"), ("shown_image", "IMAGE QUALITY CHECK")]:
            text = render_prompt(SCREENSHOT, block)
            assert "Also check the axis labels are readable." in text
            assert default in text  # added to, not replaced

    def test_replace_drops_the_defaults_for_both(self, write_prompts):
        write_prompts({"screenshot": {"replace": "Just describe the image."}})

        assert render_prompt(SCREENSHOT, "draft_review") == "Just describe the image."
        assert render_prompt(SCREENSHOT, "shown_image") == "Just describe the image."

    def test_the_internal_block_names_are_not_section_names(self, write_prompts, capsys):
        """`screenshot` is the configurable name; the blocks behind it are private."""
        write_prompts({"draft_review": "Check the axes."})

        assert "Check the axes." not in render_prompt(SCREENSHOT, "draft_review")
        assert "Unknown prompt section" in capsys.readouterr().err

    def test_sections_do_not_leak_between_templates(self, write_prompts, capsys):
        """One file addresses every template, so each must take only its own sections."""
        write_prompts({"library_selection": "ECharts only.", "screenshot": "Check the axes."})

        assert "ECharts only." in render_instructions()
        assert "Check the axes." not in render_instructions()
        assert "ECharts only." not in render_prompt(SCREENSHOT, "draft_review")
        assert "Check the axes." in render_prompt(SCREENSHOT, "draft_review")
        assert capsys.readouterr().err == "", "rendering must not fall back to the defaults"


class TestAddIsTheDefault:
    """A bare string adds to the shipped text rather than discarding it.

    Replacing a section wholesale drops operational detail the model needs (which
    libraries may be missing, above all), which is a silent way to make the server
    worse. Adding is what most house rules actually mean.
    """

    def test_default_text_survives_alongside_the_house_rule(self, write_prompts):
        write_prompts({"library_selection": "Always use ECharts."})
        text = render_instructions()

        assert "Always use ECharts." in text
        assert "PRIMARILY write visualizations with HoloViz packages" in text
        # The missing-package warning is the detail a naive replace would lose.
        assert "may be MISSING" in text

    def test_user_rules_render_before_the_shipped_text(self, write_prompts):
        """The user's rules are read first; the defaults follow as the fallback."""
        write_prompts({"library_selection": "Always use ECharts."})
        text = render_instructions()

        header_at = text.index(prompts._USER_RULES_HEADER)
        assert header_at < text.index("Always use ECharts.") < text.index("PRIMARILY write")

    def test_precedence_header_names_its_source_and_the_fallback(self, write_prompts):
        """A narrow rule must not read as licence to drop the rest of the section."""
        write_prompts({"library_selection": "Always use ECharts."})
        header = prompts._USER_RULES_HEADER

        assert "set by the person running this server" in header  # whose rules these are
        assert "these win" in header  # what happens on conflict
        assert "do not cover" in header  # defaults still apply elsewhere
        assert "below" in header  # the defaults follow, so the pointer must say so
        assert header in render_instructions()

    def test_multiple_sections_can_be_overridden_at_once(self, write_prompts):
        write_prompts({"library_selection": "Altair only.", "output": "Just print the URL."})
        text = render_instructions()

        assert "Altair only." in text
        assert "Just print the URL." in text
        assert "PRIMARILY write visualizations with HoloViz packages" in text
        assert "[Show Visualization](url)" in text

    def test_untouched_sections_still_come_from_the_template(self, write_prompts):
        write_prompts({"library_selection": "Always use ECharts."})
        text = render_instructions()

        assert "WORKFLOW:" in text
        assert "ERRORS" in text

    @pytest.mark.parametrize("section", ["intro", "workflow", "file_policy", "rendering", "errors"])
    def test_behavioural_sections_are_overridable_too(self, write_prompts, section, capsys):
        """These describe how the server works, but the user still owns them."""
        write_prompts({section: f"House rule for {section}."})
        text = render_instructions()

        assert f"House rule for {section}." in text
        assert "Unknown prompt section" not in capsys.readouterr().err

    def test_every_section_can_be_set_at_once(self, write_prompts):
        write_prompts({section: f"Rule {section}." for section in known_sections()})
        text = render_instructions()

        # `screenshot` belongs to a different template, so it must not leak in.
        for section in ["intro", "workflow", "file_policy", "library_selection", "extensions", "security", "rendering", "output", "errors"]:
            assert f"Rule {section}." in text
        assert "Rule screenshot." not in text

    def test_override_text_is_data_not_jinja(self, write_prompts):
        """A stray {{ }} in someone's prose must survive, not be evaluated."""
        write_prompts({"output": "Use {{ variable }} syntax in your templates."})
        assert "{{ variable }}" in render_instructions()

    def test_home_relative_paths_are_expanded(self, monkeypatch, tmp_path):
        """MCP configs conventionally write ~/my-prompts.json."""
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / "my-prompts.json").write_text(json.dumps({"library_selection": "Altair only."}), encoding="utf-8")
        monkeypatch.setenv(_PROMPTS_FILE_ENV, "~/my-prompts.json")

        assert "Altair only." in render_instructions()


class TestReplaceMustBeAskedFor:
    """Discarding the shipped text is available, but never the accidental outcome."""

    def test_replace_discards_the_default(self, write_prompts):
        write_prompts({"library_selection": {"replace": "LIBRARY SELECTION:\nUse plotly.express and nothing else."}})
        text = render_instructions()

        assert "Use plotly.express and nothing else." in text
        assert "PRIMARILY write visualizations with HoloViz packages" not in text
        assert prompts._USER_RULES_HEADER not in text
        assert "WORKFLOW:" in text  # other sections unaffected

    def test_explicit_add_matches_the_bare_string(self, write_prompts):
        write_prompts({"library_selection": {"add": "Always use ECharts."}})
        text = render_instructions()

        assert "Always use ECharts." in text
        assert "PRIMARILY write visualizations with HoloViz packages" in text

    def test_both_modes_at_once_is_rejected(self, write_prompts, capsys):
        write_prompts({"library_selection": {"add": "a", "replace": "b"}})

        text = render_instructions()
        assert "PRIMARILY write visualizations with HoloViz packages" in text
        assert "exactly one of 'add' or 'replace'" in capsys.readouterr().err

    def test_unknown_mode_key_is_rejected(self, write_prompts, capsys):
        write_prompts({"library_selection": {"prepend": "a"}})

        assert "PRIMARILY write visualizations with HoloViz packages" in render_instructions()
        assert "exactly one of 'add' or 'replace'" in capsys.readouterr().err


class TestBadConfigNeverBreaksStartup:
    """A broken override costs a customization, never the server (see module docstring)."""

    def test_malformed_json_falls_back_to_builtin(self, write_prompts, capsys):
        write_prompts("{not valid json")

        text = render_instructions()
        assert "PRIMARILY write visualizations with HoloViz packages" in text
        assert "not valid JSON" in capsys.readouterr().err

    def test_json_array_is_rejected(self, write_prompts, capsys):
        write_prompts(["nope"])

        assert "WORKFLOW:" in render_instructions()
        assert "must be a JSON object" in capsys.readouterr().err

    def test_non_string_section_value_is_skipped(self, write_prompts, capsys):
        write_prompts({"library_selection": 42})

        text = render_instructions()
        assert "PRIMARILY write visualizations with HoloViz packages" in text  # default kept
        assert "must be a string or an object" in capsys.readouterr().err

    def test_non_string_inside_a_mode_object_is_skipped(self, write_prompts, capsys):
        write_prompts({"library_selection": {"replace": 42}})

        assert "PRIMARILY write visualizations with HoloViz packages" in render_instructions()
        assert "replace must be a string" in capsys.readouterr().err

    def test_missing_file_warns_and_falls_back(self, monkeypatch, capsys):
        monkeypatch.setenv(_PROMPTS_FILE_ENV, "/nonexistent/path/xyz.json")

        assert "WORKFLOW:" in render_instructions()
        assert "Could not read" in capsys.readouterr().err

    def test_unknown_section_name_warns_but_still_renders(self, write_prompts, capsys):
        write_prompts({"not_a_real_section": "hello"})

        assert "WORKFLOW:" in render_instructions()
        assert "Unknown prompt section" in capsys.readouterr().err


def test_builtin_template_is_the_last_resort(write_prompts):
    """_builtin ignores all configuration by construction."""
    write_prompts({"library_selection": "ignored"})
    text = prompts._builtin(prompts.INSTRUCTIONS)
    assert "ignored" not in text
    assert "PRIMARILY write visualizations with HoloViz packages" in text
