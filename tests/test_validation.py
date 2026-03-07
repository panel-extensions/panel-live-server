"""Tests for panel_live_server.validation (Layers 1–3 + formatting)."""

import pytest

from panel_live_server.validation import SecurityError
from panel_live_server.validation import ast_check
from panel_live_server.validation import check_packages
from panel_live_server.validation import ruff_check
from panel_live_server.validation import ruff_format

# ---------------------------------------------------------------------------
# Layer 1: ast_check
# ---------------------------------------------------------------------------


class TestAstCheck:
    def test_valid_code(self):
        assert ast_check("x = 1\nprint(x)") is None

    def test_valid_multiline(self):
        assert ast_check("import pandas as pd\ndf = pd.DataFrame({'a': [1]})\ndf") is None

    def test_syntax_error_missing_colon(self):
        result = ast_check("if True\n    pass")
        assert result is not None
        assert "line 1" in result

    def test_syntax_error_unmatched_paren(self):
        assert ast_check("print((1 + 2)") is not None

    def test_syntax_error_invalid_indent(self):
        result = ast_check("  x = 1")
        assert result is not None
        assert "indent" in result.lower()

    def test_empty_code(self):
        assert ast_check("") is None

    def test_comment_only(self):
        assert ast_check("# just a comment") is None


# ---------------------------------------------------------------------------
# Layer 2: ruff_check — raises SecurityError on violations
# ---------------------------------------------------------------------------


class TestRuffCheck:
    def test_valid_code_returns_none(self):
        assert ruff_check("x = 1\nprint(x)") is None

    def test_exec_raises_security_error(self):
        with pytest.raises(SecurityError, match="exec"):
            ruff_check('exec("print(1)")')

    def test_eval_raises_security_error(self):
        with pytest.raises(SecurityError):
            ruff_check('eval("1 + 2")')

    def test_pickle_loads_raises_security_error(self):
        with pytest.raises(SecurityError):
            ruff_check("import pickle\npickle.loads(b'data')")

    def test_subprocess_shell_raises_security_error(self):
        with pytest.raises(SecurityError):
            ruff_check("import subprocess\nsubprocess.run('ls', shell=True)")

    def test_os_system_raises_security_error(self):
        with pytest.raises(SecurityError):
            ruff_check("import os\nos.system('ls')")

    def test_sql_injection_fstring_raises_security_error(self):
        code = 'bid = "123"\nq = f"SELECT * FROM t WHERE id = {bid}"\nprint(q)'
        with pytest.raises(SecurityError):
            ruff_check(code)

    def test_requests_no_timeout_raises_security_error(self):
        with pytest.raises(SecurityError):
            ruff_check("import requests\nrequests.get('http://example.com')")

    def test_requests_with_timeout_passes(self):
        code = "import requests\nrequests.get('http://example.com', timeout=10)"
        assert ruff_check(code) is None

    def test_pd_eval_passes(self):
        """pd.eval() is not the builtin eval — should not be flagged."""
        assert ruff_check("import pandas as pd\npd.eval('1 + 2')") is None

    def test_sql_parameterized_passes(self):
        assert ruff_check('q = "SELECT * FROM t WHERE id = :bid"\nprint(q)') is None

    def test_security_error_contains_line_info(self):
        with pytest.raises(SecurityError) as exc_info:
            ruff_check('exec("x")')
        assert "line" in str(exc_info.value)

    # Blocked imports — caught by AST scan, not ruff
    def test_import_pickle_blocked(self):
        with pytest.raises(SecurityError, match="pickle"):
            ruff_check("import pickle")

    def test_import_pickle_loads_blocked(self):
        with pytest.raises(SecurityError, match="pickle"):
            ruff_check("import pickle\npickle.loads(b'data')")

    def test_from_pickle_blocked(self):
        with pytest.raises(SecurityError, match="pickle"):
            ruff_check("from pickle import loads")

    def test_import_subprocess_blocked(self):
        with pytest.raises(SecurityError, match="subprocess"):
            ruff_check("import subprocess")

    def test_import_socket_blocked(self):
        with pytest.raises(SecurityError, match="socket"):
            ruff_check("import socket")

    def test_import_marshal_blocked(self):
        with pytest.raises(SecurityError, match="marshal"):
            ruff_check("import marshal")

    def test_import_threading_blocked(self):
        with pytest.raises(SecurityError, match="threading"):
            ruff_check("import threading")

    def test_blocked_import_in_visualization_code(self):
        """Real-world probe: the exact code that slipped through before."""
        code = (
            "import panel as pn\n"
            "import pickle\n"
            "pn.extension()\n"
            "payload = pickle.dumps({'status': 'hello', 'n': 3})\n"
            "obj = pickle.loads(payload)\n"
            "pn.Column(pn.pane.Markdown(f'obj: {obj}')).servable()\n"
        )
        with pytest.raises(SecurityError, match="pickle"):
            ruff_check(code)


# ---------------------------------------------------------------------------
# Layer 3: check_packages
# ---------------------------------------------------------------------------


class TestCheckPackages:
    def test_installed_package_passes(self):
        assert check_packages("import panel") is None

    def test_numpy_passes(self):
        assert check_packages("import numpy") is None

    def test_aliased_import_passes(self):
        assert check_packages("import numpy as np") is None

    def test_from_import_passes(self):
        assert check_packages("from pandas import DataFrame") is None

    def test_stdlib_not_flagged(self):
        assert check_packages("import os\nimport json\nimport sys") is None

    def test_missing_package_returns_error(self):
        result = check_packages("import _totally_fake_pkg_xyz_99")
        assert result is not None
        assert "_totally_fake_pkg_xyz_99" in result

    def test_missing_package_mentions_list_packages(self):
        result = check_packages("import _totally_fake_pkg_xyz_99")
        assert result is not None
        assert "list_packages" in result

    def test_mapping_sklearn_to_scikit_learn(self):
        """import sklearn → error should mention scikit-learn, not sklearn."""
        result = check_packages("import sklearn")
        # Only check mapping if sklearn is actually not installed
        if result is not None:
            assert "scikit-learn" in result

    def test_mapping_pil_to_pillow(self):
        """import PIL → error should mention Pillow."""
        result = check_packages("import PIL")
        if result is not None:
            assert "Pillow" in result

    def test_submodule_import_uses_top_level(self):
        """from panel.widgets import Button should check 'panel', not 'panel.widgets'."""
        assert check_packages("from panel.widgets import Button") is None

    def test_syntax_error_in_code_returns_none(self):
        """Invalid syntax should not raise — Layer 1 handles that."""
        assert check_packages("def foo(\n    pass") is None

    def test_multiple_imports_first_missing_reported(self):
        result = check_packages("import panel\nimport _fake_xyz_abc")
        assert result is not None
        assert "_fake_xyz_abc" in result


# ---------------------------------------------------------------------------
# Formatting: ruff_format
# ---------------------------------------------------------------------------


class TestRuffFormat:
    def test_formats_messy_code(self):
        messy = "x=1+2\ny = [1,2,  3]\nprint(  x,y )"
        result = ruff_format(messy)
        assert "x = 1 + 2" in result
        assert "[1, 2, 3]" in result

    def test_preserves_clean_code(self):
        clean = "x = 1\nprint(x)\n"
        assert ruff_format(clean) == clean

    def test_returns_original_on_syntax_error(self):
        broken = "def foo(\n    pass"
        assert ruff_format(broken) == broken
