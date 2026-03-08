"""Tests for view_page extension loading and execution."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from panel_live_server.database import Snippet
from panel_live_server.database import SnippetDatabase


def _make_snippet(app: str, method: str = "jupyter") -> Snippet:
    return Snippet(app=app, method=method, name="test")


class TestExecuteCodeNoPreambleInjection:
    """_execute_code no longer injects pn.extension() — create_view() handles it."""

    def test_jupyter_app_has_no_extension_call(self):
        """_execute_code does not inject pn.extension() into the executed code string."""
        from panel_live_server.pages.view_page import _execute_code

        snippet = _make_snippet("x = 1  # plotly visualization")
        captured = {}

        def fake_extract(code):
            captured["app"] = code
            return (code, "")

        with (
            patch("panel_live_server.pages.view_page.extract_last_expression", side_effect=fake_extract),
            patch("panel_live_server.pages.view_page.execute_in_module", return_value={}),
            patch("panel_live_server.pages.view_page.sys") as mock_sys,
            patch("panel_live_server.pages.view_page.pn"),
        ):
            mock_sys.modules = {}
            _execute_code(snippet)

        # Extension injection is now done at the session level in create_view(),
        # NOT inside the executed code string.
        assert "pn.extension('plotly')" not in captured.get("app", "")

    def test_panel_method_code_unchanged(self):
        """panel method passes code through without extra extension calls."""
        from panel_live_server.pages.view_page import _execute_code

        snippet = _make_snippet(
            "import panel as pn\npn.extension('plotly')\nx = 1",
            method="panel",
        )
        captured = {}

        def fake_execute(code, module_name, *, cleanup):
            captured["code"] = code
            return {}

        with patch("panel_live_server.pages.view_page.execute_in_module", side_effect=fake_execute):
            _execute_code(snippet)

        # Exactly the one pn.extension() call the user wrote, nothing extra injected.
        assert captured.get("code", "").count("pn.extension(") == 1


class TestCreateViewExtensionLoading:
    """create_view() loads detected extensions at the Panel session level."""

    @pytest.fixture
    def temp_db(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        db = SnippetDatabase(db_path)
        yield db
        db_path.unlink(missing_ok=True)

    def test_plotly_extension_loaded_at_session_level(self, temp_db):
        """create_view() calls pn.extension('plotly', 'codeeditor') for plotly code."""
        from panel_live_server.pages.view_page import create_view

        snippet = temp_db.create_snippet(_make_snippet("x = 1  # plotly visualization"))
        pn_mock = MagicMock()

        with (
            patch("panel_live_server.pages.view_page.get_db", return_value=temp_db),
            patch("panel_live_server.pages.view_page.pn", pn_mock),
            patch("panel_live_server.pages.view_page._execute_code", return_value=MagicMock()),
        ):
            create_view(snippet.id)

        # pn.extension should have been called once with all detected extensions.
        pn_mock.extension.assert_called_once()
        args = set(pn_mock.extension.call_args[0])
        assert "codeeditor" in args
        assert "plotly" in args

    def test_no_extra_extensions_for_plain_code(self, temp_db):
        """create_view() only loads codeeditor for plain code with no special extensions."""
        from panel_live_server.pages.view_page import create_view

        snippet = temp_db.create_snippet(_make_snippet("x = 1 + 2"))
        pn_mock = MagicMock()

        with (
            patch("panel_live_server.pages.view_page.get_db", return_value=temp_db),
            patch("panel_live_server.pages.view_page.pn", pn_mock),
            patch("panel_live_server.pages.view_page._execute_code", return_value=MagicMock()),
        ):
            create_view(snippet.id)

        pn_mock.extension.assert_called_once()
        args = set(pn_mock.extension.call_args[0])
        assert "codeeditor" in args
        assert "plotly" not in args

    def test_multiple_extensions_loaded(self, temp_db):
        """create_view() loads all detected extensions in a single pn.extension() call."""
        from panel_live_server.pages.view_page import create_view

        snippet = temp_db.create_snippet(_make_snippet("x = 1  # plotly altair chart"))
        pn_mock = MagicMock()

        with (
            patch("panel_live_server.pages.view_page.get_db", return_value=temp_db),
            patch("panel_live_server.pages.view_page.pn", pn_mock),
            patch("panel_live_server.pages.view_page._execute_code", return_value=MagicMock()),
        ):
            create_view(snippet.id)

        pn_mock.extension.assert_called_once()
        args = set(pn_mock.extension.call_args[0])
        assert "plotly" in args
        assert "vega" in args
        assert "codeeditor" in args
