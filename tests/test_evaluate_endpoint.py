"""Tests for POST /api/evaluate — running code for its text output, not a picture."""

import json
import sys

from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application

from panel_live_server.endpoints import EvaluateEndpoint


class TestEvaluateEndpoint(AsyncHTTPTestCase):
    """The endpoint returns stdout and the last expression, and never renders."""

    def get_app(self) -> Application:
        return Application([(r"/api/evaluate", EvaluateEndpoint)])

    def _post(self, body: dict):
        return self.fetch(
            "/api/evaluate",
            method="POST",
            body=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )

    def _json(self, body: dict) -> dict:
        response = self._post(body)
        assert response.code == 200, response.body
        return json.loads(response.body)

    def test_returns_the_last_expression(self) -> None:
        """A trailing expression is the answer, so no print should be required."""
        payload = self._json({"code": "x = 6 * 7\nx"})
        assert payload["result"] == "42"
        assert payload["error"] == ""

    def test_captures_print_output(self) -> None:
        payload = self._json({"code": "print('hello')\nprint('again')\n1 + 1"})
        assert payload["stdout"] == "hello\nagain\n"
        assert payload["result"] == "2"

    def test_captures_stderr(self) -> None:
        payload = self._json({"code": "import sys\nsys.stderr.write('warned\\n')"})
        assert "warned" in payload["stdout"]

    def test_none_result_is_not_reported(self) -> None:
        """Reporting ``None`` for a statement-like final line would be noise."""
        payload = self._json({"code": "y = 1"})
        assert payload["result"] == ""

    def test_exception_returns_error_and_keeps_prior_output(self) -> None:
        """Whatever was printed before the failure is the useful context."""
        payload = self._json({"code": "print('before boom')\nraise ValueError('kaboom')"})
        assert "before boom" in payload["stdout"]
        assert payload["error"] == "ValueError: kaboom"
        assert "ValueError: kaboom" in payload["traceback"]

    def test_syntax_error_is_a_400(self) -> None:
        """Caught statically so it comes back fixable rather than as a traceback."""
        response = self._post({"code": "def broken(:"})
        assert response.code == 400
        assert json.loads(response.body)["error"] == "SyntaxError"

    def test_missing_code_is_a_400(self) -> None:
        response = self._post({})
        assert response.code == 400

    def test_non_json_body_is_a_400(self) -> None:
        response = self.fetch("/api/evaluate", method="POST", body="not json")
        assert response.code == 400

    def test_evaluations_do_not_share_state(self) -> None:
        """Each call gets a fresh module, so one snippet cannot see another's names."""
        assert self._json({"code": "secret = 99\nsecret"})["result"] == "99"
        assert self._json({"code": "secret"})["error"].startswith("NameError")

    def test_module_is_not_left_in_sys_modules(self) -> None:
        self._json({"code": "z = 1\nz"})
        assert not [name for name in sys.modules if name.startswith("pls_eval_")]

    def test_raising_statements_do_not_leak_the_module(self) -> None:
        """The cleanup must cover the exec, not just the trailing eval.

        ``execute_in_module`` is called with ``cleanup=False`` so its namespace
        survives for the eval, which means it does not unregister the module when
        the statements themselves raise. Without a ``finally`` around the exec,
        every failing evaluation left a ``pls_eval_*`` module behind for the life
        of the process.
        """
        payload = self._json({"code": "raise ValueError('kaboom')"})
        assert payload["error"] == "ValueError: kaboom"
        assert not [name for name in sys.modules if name.startswith("pls_eval_")]
