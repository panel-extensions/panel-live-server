"""Tests for display REST API endpoints."""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application

import panel_live_server.endpoints as endpoints_module
import panel_live_server.screenshot as screenshot_module
from panel_live_server import usage
from panel_live_server.database import SnippetDatabase
from panel_live_server.endpoints import HealthEndpoint, ScreenshotEndpoint, SnippetEditEndpoint, SnippetEndpoint
from panel_live_server.validation import SecurityError


class _FakeDB:
    """Minimal fake DB for endpoint tests."""

    def __init__(self) -> None:
        self.method_seen: str | None = None
        self.flags_seen: dict[str, bool] = {}
        self.raise_value_error: bool = False
        self.promoted: tuple[str, str | None, str | None] | None = None
        self.promote_error: str = ""

    def create_visualization(
        self,
        app: str,
        name: str = "",
        description: str = "",
        method: str = "inline",
        run_static: bool = True,
        format: bool = True,
        execute: bool = True,
        draft: bool = False,
    ) -> SimpleNamespace:
        self.method_seen = method
        self.flags_seen = {"run_static": run_static, "format": format, "execute": execute}
        if self.raise_value_error:
            raise ValueError("Unsupported execution method 'invalid'. Supported methods: jupyter, panel, pyodide")
        return SimpleNamespace(id="snippet-123", app=app, error_message=None)

    def promote_draft(self, snippet_id: str, name: str | None = None, description: str | None = None) -> SimpleNamespace:
        self.promoted = (snippet_id, name, description)
        if self.promote_error:
            raise ValueError(self.promote_error)
        return SimpleNamespace(id=snippet_id, app="promoted = 1\n", error_message=None)


class TestSnippetEndpoint(AsyncHTTPTestCase):
    """Endpoint tests for /api/snippet."""

    def setUp(self) -> None:
        self.fake_db = _FakeDB()
        self._original_get_db = endpoints_module.get_db
        endpoints_module.get_db = lambda: self.fake_db
        super().setUp()

    def tearDown(self) -> None:
        endpoints_module.get_db = self._original_get_db
        super().tearDown()

    def get_app(self) -> Application:
        return Application(
            [
                (r"/api/snippet", SnippetEndpoint),
                (r"/api/health", HealthEndpoint),
            ]
        )

    def test_create_snippet_accepts_pyodide_method(self) -> None:
        """POST /api/snippet accepts pyodide and returns a URL payload."""
        body = {
            "code": "print('hello')",
            "name": "Pyodide test",
            "description": "Smoke test",
            "method": "pyodide",
        }

        response = self.fetch(
            "/api/snippet",
            method="POST",
            body=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )

        assert response.code == 200
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["id"] == "snippet-123"
        assert "view?id=snippet-123" in payload["url"]
        assert self.fake_db.method_seen == "pyodide"

    def test_create_snippet_invalid_method_returns_400(self) -> None:
        """POST /api/snippet maps ValueError to HTTP 400."""
        self.fake_db.raise_value_error = True

        body = {
            "code": "print('hello')",
            "method": "invalid",
        }

        response = self.fetch(
            "/api/snippet",
            method="POST",
            body=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )

        assert response.code == 400
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["error"] == "ValueError"
        assert "Unsupported execution method" in payload["message"]

    def test_create_snippet_uses_codespaces_url_when_available(self) -> None:
        """POST /api/snippet should return Codespaces-forwarded URL when config.external_url is set."""
        body = {
            "code": "print('hello')",
            "method": "inline",
        }

        fake_config = SimpleNamespace(external_url="https://literate-chainsaw-54wjwvrrxv4c4p5q-5077.app.github.dev")

        with patch.object(endpoints_module, "get_config", return_value=fake_config):
            response = self.fetch(
                "/api/snippet",
                method="POST",
                body=json.dumps(body),
                headers={"Content-Type": "application/json"},
            )

        assert response.code == 200
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["url"] == "https://literate-chainsaw-54wjwvrrxv4c4p5q-5077.app.github.dev/view?id=snippet-123"

    def test_create_snippet_uses_jupyter_proxy_external_url(self) -> None:
        """POST /api/snippet should use external_url from config when set to a Jupyter proxy URL."""
        body = {
            "code": "print('hello')",
            "method": "inline",
        }

        fake_config = SimpleNamespace(external_url="https://proxy.example.dev/user/foo/proxy/5077")

        with patch.object(endpoints_module, "get_config", return_value=fake_config):
            response = self.fetch(
                "/api/snippet",
                method="POST",
                body=json.dumps(body),
                headers={"Content-Type": "application/json"},
            )

        assert response.code == 200
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["url"] == "https://proxy.example.dev/user/foo/proxy/5077/view?id=snippet-123"

    def test_create_snippet_uses_configured_external_url(self) -> None:
        """config.external_url should be used for URL construction when set."""
        body = {
            "code": "print('hello')",
            "method": "inline",
        }

        fake_config = SimpleNamespace(external_url="https://config-proxy.example.dev/user/proxy/5077")

        with patch.object(endpoints_module, "get_config", return_value=fake_config):
            response = self.fetch(
                "/api/snippet",
                method="POST",
                body=json.dumps(body),
                headers={"Content-Type": "application/json"},
            )

        assert response.code == 200
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["url"] == "https://config-proxy.example.dev/user/proxy/5077/view?id=snippet-123"

    def test_draft_id_promotes_instead_of_storing_new_code(self) -> None:
        """Promotion must not go anywhere near create_visualization.

        This is where the last redundant execution is removed: the draft has
        already rendered, so showing it is a flag flip, not a second store-and-run.
        """
        body = {"draft_id": "draft-9", "name": "Final", "description": "Done"}

        response = self.fetch(
            "/api/snippet",
            method="POST",
            body=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )

        assert response.code == 200
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["id"] == "draft-9"
        assert "view?id=draft-9" in payload["url"]
        assert self.fake_db.promoted == ("draft-9", "Final", "Done")
        assert self.fake_db.flags_seen == {}, "promotion must not create a new visualization"

    def test_promotion_carries_no_code_echo(self) -> None:
        """The payload stopped shipping code, so the promotion response must too.

        The App fetches it from GET /api/snippet when the panel is opened.
        """
        response = self.fetch(
            "/api/snippet",
            method="POST",
            body=json.dumps({"draft_id": "draft-9"}),
            headers={"Content-Type": "application/json"},
        )

        payload = json.loads(response.body.decode("utf-8"))
        assert "code" not in payload
        assert payload["id"] == "draft-9"

    def test_rejected_promotion_returns_400(self) -> None:
        """A stale or already-shown draft is a 400 the model can read and recover from."""
        self.fake_db.promote_error = "No draft found with id 'draft-9'."

        response = self.fetch(
            "/api/snippet",
            method="POST",
            body=json.dumps({"draft_id": "draft-9"}),
            headers={"Content-Type": "application/json"},
        )

        assert response.code == 400
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["error"] == "ValueError"
        assert "No draft found" in payload["message"]


class _FakeDraftDB:
    """Fake DB standing in for the draft lifecycle a screenshot performs."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.deleted: list[str] = []
        self.flags_seen: dict[str, bool] = {}
        self.swept: list[float] = []
        # What the render is taken to have stamped on the row, read back after capture.
        self.rendered_status: str = "success"
        self.error_message: str | None = None
        self.raise_exc: Exception | None = None
        self.snippet_exists: bool = False

    def create_visualization(
        self,
        app: str,
        name: str = "",
        description: str = "",
        method: str = "inline",
        run_static: bool = True,
        format: bool = True,
        execute: bool = True,
        draft: bool = False,
    ) -> SimpleNamespace:
        self.created.append(app)
        self.flags_seen = {"run_static": run_static, "format": format, "execute": execute, "draft": draft}
        if self.raise_exc:
            raise self.raise_exc
        self.snippet_exists = True
        return SimpleNamespace(id="draft-1", status="pending", error_message=None)

    def delete_snippet(self, snippet_id: str) -> bool:
        self.deleted.append(snippet_id)
        self.snippet_exists = False
        return True

    def delete_stale_drafts(self, older_than_hours: float) -> int:
        self.swept.append(older_than_hours)
        return 0

    def get_snippet(self, snippet_id: str) -> SimpleNamespace | None:
        if not self.snippet_exists:
            return None
        return SimpleNamespace(id=snippet_id, status=self.rendered_status, error_message=self.error_message)


class TestScreenshotEndpointDrafts(AsyncHTTPTestCase):
    """POST /api/screenshot renders code that was never shown, then discards it (issue #43)."""

    def setUp(self) -> None:
        self.fake_db = _FakeDraftDB()
        self._original_get_db = endpoints_module.get_db
        endpoints_module.get_db = lambda: self.fake_db
        super().setUp()

    def tearDown(self) -> None:
        endpoints_module.get_db = self._original_get_db
        super().tearDown()

    def get_app(self) -> Application:
        return Application([(r"/api/screenshot", ScreenshotEndpoint)])

    def _post(self, body: dict) -> object:
        return self.fetch(
            "/api/screenshot",
            method="POST",
            body=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )

    def test_draft_is_captured_and_retained_with_its_id(self) -> None:
        """A successful draft is kept, and its id comes back for show(draft_id=...).

        Deleting it here is what used to force the final show to store and execute
        the same code all over again.
        """
        captured: dict[str, str] = {}

        async def fake_capture(url, **kwargs):
            captured["url"] = url
            return screenshot_module.Capture(images=[("", b"\x89PNG-bytes")])

        with patch("panel_live_server.screenshot.capture_pages", fake_capture):
            response = self._post({"code": "1 + 1", "method": "inline"})

        assert response.code == 200
        assert response.headers["Content-Type"] == "image/png"
        assert response.body == b"\x89PNG-bytes"
        assert response.headers["X-PLS-Draft-Id"] == "draft-1"
        assert "view?id=draft-1" in captured["url"]
        assert self.fake_db.created == ["1 + 1"]
        assert self.fake_db.deleted == [], "a good draft must survive for promotion"

    def test_draft_is_stored_without_being_executed_or_formatted(self) -> None:
        """The draft path must not run the code: the render that follows is the detector.

        This is the halving of the draft loop. Executing at storage time and then
        rendering runs every draft twice for one picture. format=False keeps the
        stored text identical to what the model sent.
        """

        async def fake_capture(url, **kwargs):
            return screenshot_module.Capture(images=[("", b"\x89PNG")])

        with patch("panel_live_server.screenshot.capture_pages", fake_capture):
            response = self._post({"code": "1 + 1", "method": "inline"})

        assert response.code == 200
        assert self.fake_db.flags_seen == {"run_static": True, "format": False, "execute": False, "draft": True}

    def test_stale_drafts_are_swept_on_the_way_in(self) -> None:
        """Retained drafts need an expiry, and activity is when it is worth applying."""

        async def fake_capture(url, **kwargs):
            return screenshot_module.Capture(images=[("", b"\x89PNG")])

        with patch("panel_live_server.screenshot.capture_pages", fake_capture):
            self._post({"code": "1 + 1"})

        assert self.fake_db.swept, "expected a sweep before the draft was stored"

    def test_draft_is_deleted_even_when_capture_fails(self) -> None:
        """A capture that blows up must not strand the draft in the database."""

        async def boom(url, **kwargs):
            raise RuntimeError("browser exploded")

        with patch("panel_live_server.screenshot.capture_pages", boom):
            response = self._post({"code": "1 + 1"})

        assert response.code == 500
        assert self.fake_db.deleted == ["draft-1"]

    def test_runtime_error_returns_400_and_deletes_the_draft(self) -> None:
        """A draft that raised comes back as its traceback, not as a picture of one.

        The verdict is now read off the row *after* the capture, because the render
        is what discovers the failure and writes it there.
        """
        self.fake_db.rendered_status = "error"
        self.fake_db.error_message = "NameError: name 'nope' is not defined"

        async def fake_capture(url, **kwargs):
            return screenshot_module.Capture(images=[("", b"\x89PNG")])

        with patch("panel_live_server.screenshot.capture_pages", fake_capture):
            response = self._post({"code": "nope"})

        assert response.code == 400
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["error"] == "RuntimeError"
        assert "NameError" in payload["message"]
        assert self.fake_db.deleted == ["draft-1"]

    def test_security_error_returns_400(self) -> None:
        """A blocked import is reported before anything is rendered."""
        self.fake_db.raise_exc = SecurityError("pickle is not allowed")

        response = self._post({"code": "import pickle"})

        assert response.code == 400
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["error"] == "SecurityError"
        assert self.fake_db.deleted == []

    def test_missing_code_returns_400(self) -> None:
        response = self._post({"method": "inline"})

        assert response.code == 400
        assert self.fake_db.created == []

    def test_get_with_unknown_id_still_404s(self) -> None:
        """The shared-capture refactor must not lose the existing id check."""
        response = self.fetch("/api/screenshot?id=nope")

        assert response.code == 404

    def test_the_default_capture_is_one_screen_of_the_current_page(self) -> None:
        """Capturing everything by default is what floods a caller's context."""
        seen: dict = {}

        async def fake_capture(url, **kwargs):
            seen.update(kwargs)
            return screenshot_module.Capture(images=[("", b"\x89PNG")])

        with patch("panel_live_server.screenshot.capture_pages", fake_capture):
            self._post({"code": "1 + 1"})

        assert seen["full_page"] is False
        assert seen["do"] is None

    def test_naming_a_control_the_page_lacks_is_a_400_listing_the_real_ones(self) -> None:
        """A fixable mistake, so it must not arrive as a 500 with a traceback."""

        async def fake_capture(url, **kwargs):
            raise screenshot_module.ActionError("No element matches 'Profit'. On this page: Sales, Costs.")

        with patch("panel_live_server.screenshot.capture_pages", fake_capture):
            response = self._post({"code": "1 + 1", "do": [{"click": "Profit"}]})

        assert response.code == 400
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["error"] == "ActionError"
        assert "Sales, Costs" in payload["message"]

    def test_an_ordinary_value_error_is_still_a_500(self) -> None:
        """Catching bare ValueError here would report our own bugs as the caller's."""

        async def boom(url, **kwargs):
            raise ValueError("something inside the capture is broken")

        with patch("panel_live_server.screenshot.capture_pages", boom):
            response = self._post({"code": "1 + 1"})

        assert response.code == 500

    def test_several_images_come_back_as_json_not_one_png(self) -> None:
        """There is no honest way to put several pictures in one image body."""

        async def fake_capture(url, **kwargs):
            return screenshot_module.Capture(images=[("screen 1 of 2", b"\x89A"), ("screen 2 of 2", b"\x89B")], total_tiles=2, captured_tiles=2)

        with patch("panel_live_server.screenshot.capture_pages", fake_capture):
            response = self._post({"code": "1 + 1", "full_page": True})

        assert response.code == 200
        assert response.headers["Content-Type"].startswith("application/json")
        assert [i["label"] for i in json.loads(response.body.decode("utf-8"))["images"]] == ["screen 1 of 2", "screen 2 of 2"]

    def test_the_report_rides_along_with_a_single_png(self) -> None:
        """One image plus 'there are three more pages' is the whole point."""

        async def fake_capture(url, **kwargs):
            return screenshot_module.Capture(images=[("", b"\x89PNG")], controls=["Sales", "Costs"], total_tiles=3, captured_tiles=1)

        with patch("panel_live_server.screenshot.capture_pages", fake_capture):
            response = self._post({"code": "1 + 1"})

        assert response.headers["Content-Type"] == "image/png"
        meta = screenshot_module.decode_meta(response.headers[screenshot_module.META_HEADER])
        assert meta["controls"] == ["Sales", "Costs"]
        assert meta["total_tiles"] == 3


class TestScreenshotDraftLeavesNoTrace(AsyncHTTPTestCase):
    """A draft is retained for promotion but must stay invisible to the user."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = SnippetDatabase(Path(self._tmp.name) / "drafts.db")
        self._original_get_db = endpoints_module.get_db
        endpoints_module.get_db = lambda: self.db
        super().setUp()

    def tearDown(self) -> None:
        endpoints_module.get_db = self._original_get_db
        super().tearDown()
        self._tmp.cleanup()

    def get_app(self) -> Application:
        return Application([(r"/api/screenshot", ScreenshotEndpoint)])

    def test_draft_never_reaches_the_feed(self) -> None:
        """This is the whole point of issue #43: reviewing a draft must stay invisible.

        The row now survives the capture so it can be promoted, which means being
        invisible has to come from the draft flag rather than from deleting it.
        """
        seen_ids = []

        async def fake_capture(url, **kwargs):
            # Mid-capture the row must exist — the browser has to have a page to load.
            seen_ids.append([s.id for s in self.db.list_snippets(include_drafts=True)])
            return screenshot_module.Capture(images=[("", b"\x89PNG")])

        assert self.db.list_snippets() == []

        with patch("panel_live_server.screenshot.capture_pages", fake_capture):
            response = self.fetch(
                "/api/screenshot",
                method="POST",
                body=json.dumps({"code": "1 + 1", "name": "Draft", "method": "inline"}),
                headers={"Content-Type": "application/json"},
            )

        assert response.code == 200
        assert len(seen_ids[0]) == 1, "the draft must exist while the browser loads it"
        assert self.db.list_snippets() == [], "the draft must stay out of the feed"
        assert self.db.search_snippets("Draft") == [], "the draft must stay out of search"

        retained = self.db.list_snippets(include_drafts=True)
        assert [s.id for s in retained] == seen_ids[0], "the draft must survive for promotion"

    def test_promoting_the_draft_makes_it_visible(self) -> None:
        """Promotion is the moment a draft becomes the user's, without being re-run."""

        async def fake_capture(url, **kwargs):
            return screenshot_module.Capture(images=[("", b"\x89PNG")])

        with patch("panel_live_server.screenshot.capture_pages", fake_capture):
            response = self.fetch(
                "/api/screenshot",
                method="POST",
                body=json.dumps({"code": "1+1", "name": "Draft", "method": "inline"}),
                headers={"Content-Type": "application/json"},
            )

        draft_id = response.headers["X-PLS-Draft-Id"]
        # The capture is faked, so /view never ran; stand in for what it stamps.
        self.db.update_snippet(draft_id, status="success")

        promoted = self.db.promote_draft(draft_id, name="Final")

        assert promoted.draft is False
        assert promoted.name == "Final"
        assert [s.id for s in self.db.list_snippets()] == [draft_id]
        assert [s.id for s in self.db.search_snippets("Final")] == [draft_id]
        # Stored byte-identical to what was sent, at every stage. Reformatting here
        # would silently break a later old_str edit against this snippet.
        assert promoted.app == "1+1"


class TestSnippetEditEndpoint(AsyncHTTPTestCase):
    """POST /api/snippet/edit changes stored code without the whole snippet being resent."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = SnippetDatabase(Path(self._tmp.name) / "drafts.db")
        self._original_get_db = endpoints_module.get_db
        endpoints_module.get_db = lambda: self.db
        super().setUp()

    def tearDown(self) -> None:
        endpoints_module.get_db = self._original_get_db
        super().tearDown()
        self._tmp.cleanup()

    def get_app(self) -> Application:
        return Application([(r"/api/snippet/edit", SnippetEditEndpoint)])

    def _draft(self, code: str):
        # run_static=False: these tests are about editing, not about validation, and
        # the package check would otherwise depend on what happens to be installed.
        return self.db.create_visualization(app=code, name="Draft", run_static=False, format=False, execute=False, draft=True)

    def _edit(self, **body) -> object:
        # The endpoint keys on snippet_id: a shown snippet is editable now, so naming
        # the parameter draft_id would describe only half of what it accepts.
        return self.fetch(
            "/api/snippet/edit",
            method="POST",
            body=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )

    def test_single_occurrence_is_replaced(self) -> None:
        draft = self._draft("color = 'red'\nx = 1")

        response = self._edit(snippet_id=draft.id, old_str="'red'", new_str="'blue'")

        assert response.code == 200
        assert self.db.get_snippet(draft.id).app == "color = 'blue'\nx = 1"

    def test_response_carries_no_code_echo(self) -> None:
        """Echoing the snippet back would spend exactly what the edit just saved."""
        draft = self._draft("color = 'red'")

        response = self._edit(snippet_id=draft.id, old_str="'red'", new_str="'blue'")

        payload = json.loads(response.body.decode("utf-8"))
        assert set(payload) == {"id", "chars", "forked"}
        assert payload["chars"] == len("color = 'blue'")

    def test_omitted_new_str_deletes(self) -> None:
        draft = self._draft("x = 1  # note\ny = 2")

        response = self._edit(snippet_id=draft.id, old_str="  # note")

        assert response.code == 200
        assert self.db.get_snippet(draft.id).app == "x = 1\ny = 2"

    def test_ambiguous_match_is_refused(self) -> None:
        """Silently editing the first of several matches is the wrong guess to make."""
        draft = self._draft("a = 1\nb = 1\n")

        response = self._edit(snippet_id=draft.id, old_str="= 1", new_str="= 2")

        assert response.code == 400
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["error"] == "AmbiguousMatch"
        assert "2 times" in payload["message"]
        assert self.db.get_snippet(draft.id).app == "a = 1\nb = 1\n", "draft must be untouched"

    def test_missing_match_is_refused(self) -> None:
        draft = self._draft("x = 1")

        response = self._edit(snippet_id=draft.id, old_str="nope", new_str="y")

        assert response.code == 400
        assert json.loads(response.body.decode("utf-8"))["error"] == "NoMatch"
        assert self.db.get_snippet(draft.id).app == "x = 1"

    def test_edit_that_breaks_syntax_is_refused(self) -> None:
        """Cheaper to say so here than to find out by launching Chromium."""
        draft = self._draft("x = (1 + 2)")

        response = self._edit(snippet_id=draft.id, old_str="(1 + 2)", new_str="(1 + 2")

        assert response.code == 400
        assert json.loads(response.body.decode("utf-8"))["error"] == "SyntaxError"
        assert self.db.get_snippet(draft.id).app == "x = (1 + 2)", "a refused edit must not land"

    def test_editing_a_shown_snippet_forks_it_instead(self) -> None:
        """Nothing should change under a user who is already looking at it.

        Refusing outright honoured that rule but wasted the call: the common shape
        is show → "tweak this", and the model had to resend the whole snippet. The
        edit now lands on a fresh draft, so the live row is still untouched.
        """
        snippet = self.db.create_visualization(app="x = 1", name="Chart", run_static=False, format=False, execute=False, draft=False)

        response = self._edit(snippet_id=snippet.id, old_str="1", new_str="2")

        assert response.code == 200
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["forked"] is True
        assert payload["id"] != snippet.id

        assert self.db.get_snippet(snippet.id).app == "x = 1", "the shown snippet must not move"

        fork = self.db.get_snippet(payload["id"])
        assert fork.app == "x = 2"
        assert fork.draft is True, "the fork must stay out of the feed until it is shown"
        assert fork.name == "Chart", "a forked version belongs to the same visualization"

    def test_editing_a_draft_reports_no_fork(self) -> None:
        """In-place edits must be distinguishable, or the model screenshots the wrong id."""
        draft = self._draft("x = 1")

        response = self._edit(snippet_id=draft.id, old_str="1", new_str="2")

        payload = json.loads(response.body.decode("utf-8"))
        assert payload["forked"] is False
        assert payload["id"] == draft.id

    def test_edit_that_breaks_at_runtime_is_refused(self) -> None:
        """The edit runs before it returns, so a broken one never becomes a showable id.

        Without this the model gets an id back, shows it, and the user is the one
        who discovers the traceback.
        """
        snippet = self.db.create_visualization(app="x = 1", run_static=False, format=False, execute=False, draft=False)

        response = self._edit(snippet_id=snippet.id, old_str="1", new_str="undefined_name")

        assert response.code == 400
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["error"] == "RuntimeError"
        assert "no longer runs" in payload["message"]
        assert self.db.get_snippet(snippet.id).app == "x = 1", "the shown snippet must not move"
        assert self.db.list_snippets(include_drafts=True) == [self.db.get_snippet(snippet.id)], "no fork should be left behind"

    def test_edited_code_is_ready_to_show_without_a_screenshot(self) -> None:
        """A fork must come back promotable, or every tweak costs a Playwright launch.

        promote_draft gates on status == "success"; the edit runs the code so that
        gate is already satisfied.
        """
        snippet = self.db.create_visualization(app="x = 1", name="Chart", run_static=False, format=False, execute=False, draft=False)

        payload = json.loads(self._edit(snippet_id=snippet.id, old_str="1", new_str="2").body.decode("utf-8"))

        assert self.db.get_snippet(payload["id"]).status == "success"
        promoted = self.db.promote_draft(payload["id"])
        assert promoted.draft is False
        assert promoted.app == "x = 2"

    def test_unknown_snippet_is_404(self) -> None:
        response = self._edit(snippet_id="nope", old_str="a", new_str="b")

        assert response.code == 404

    def test_editing_keeps_the_draft_out_of_search(self) -> None:
        """Editing writes an indexed column, so the FTS triggers fire on this path."""
        draft = self._draft("import pandas")

        self._edit(snippet_id=draft.id, old_str="pandas", new_str="numpy")

        assert self.db.search_snippets("numpy") == []
        assert [s.id for s in self.db.search_snippets("numpy", include_drafts=True)] == [draft.id]
        assert self.db.search_snippets("pandas", include_drafts=True) == []


class TestSnippetGet(AsyncHTTPTestCase):
    """GET /api/snippet?id= is what replaced the code echo in the show payload."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = SnippetDatabase(Path(self._tmp.name) / "snippets.db")
        self._original_get_db = endpoints_module.get_db
        endpoints_module.get_db = lambda: self.db
        super().setUp()

    def tearDown(self) -> None:
        endpoints_module.get_db = self._original_get_db
        super().tearDown()
        self._tmp.cleanup()

    def get_app(self) -> Application:
        return Application([(r"/api/snippet", SnippetEndpoint)])

    def test_returns_the_stored_code(self) -> None:
        snippet = self.db.create_visualization(app="x = 1", name="Chart", run_static=False, format=False, execute=False)

        response = self.fetch(f"/api/snippet?id={snippet.id}")

        assert response.code == 200
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["code"].strip() == "x = 1"
        assert payload["name"] == "Chart"
        assert payload["id"] == snippet.id

    def test_code_is_formatted_on_read_but_stored_verbatim(self) -> None:
        """Humans get tidy code; the stored text stays matchable by old_str.

        Formatting at storage is what made an edit against a shown snippet miss:
        the author matches what it wrote, the row holds what ruff produced.
        """
        snippet = self.db.create_visualization(app="x=1+2", run_static=False, format=False, execute=False)

        payload = json.loads(self.fetch(f"/api/snippet?id={snippet.id}").body.decode("utf-8"))

        assert payload["code"] == "x = 1 + 2\n", "the panel should show formatted code"
        assert self.db.get_snippet(snippet.id).app == "x=1+2", "storage must not be reformatted"

    def test_is_readable_cross_origin(self) -> None:
        """show.html runs on the host's origin, not this server's, so it needs CORS.

        Without the header the code panel silently fails in every MCP client.
        """
        snippet = self.db.create_visualization(app="x = 1", run_static=False, format=False, execute=False)

        response = self.fetch(f"/api/snippet?id={snippet.id}")

        assert response.headers["Access-Control-Allow-Origin"] == "*"

    def test_unknown_id_is_404(self) -> None:
        assert self.fetch("/api/snippet?id=nope").code == 404

    def test_missing_id_is_400(self) -> None:
        assert self.fetch("/api/snippet").code == 400


class TestUsageReporting(AsyncHTTPTestCase):
    """/api/health carries the session's usage counters (issue #58)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = SnippetDatabase(Path(self._tmp.name) / "snippets.db")
        self._original_get_db = endpoints_module.get_db
        endpoints_module.get_db = lambda: self.db
        usage.reset()
        super().setUp()

    def tearDown(self) -> None:
        endpoints_module.get_db = self._original_get_db
        usage.reset()
        super().tearDown()
        self._tmp.cleanup()

    def get_app(self) -> Application:
        return Application(
            [
                (r"/api/snippet", SnippetEndpoint),
                (r"/api/health", HealthEndpoint),
            ]
        )

    def _health(self) -> dict:
        return json.loads(self.fetch("/api/health").body.decode("utf-8"))

    def test_health_reports_usage(self) -> None:
        assert self._health()["usage"]["total_calls"] == 0

    def test_showing_code_is_counted(self) -> None:
        code = "x = 1"
        self.fetch(
            "/api/snippet",
            method="POST",
            body=json.dumps({"code": code, "validated": True}),
            headers={"Content-Type": "application/json"},
        )

        by_tool = self._health()["usage"]["by_tool"]
        assert by_tool["show"] == {"chars": len(code), "calls": 1}

    def test_promotion_is_counted_as_a_call_that_sent_nothing(self) -> None:
        """The saving only shows up if the free call is counted alongside the paid ones."""
        draft = self.db.create_visualization(app="x = 1", run_static=False, format=False, execute=False, draft=True)
        self.db.update_snippet(draft.id, status="success")
        usage.reset()

        response = self.fetch(
            "/api/snippet",
            method="POST",
            body=json.dumps({"draft_id": draft.id}),
            headers={"Content-Type": "application/json"},
        )

        assert response.code == 200
        by_tool = self._health()["usage"]["by_tool"]
        assert by_tool["promote"] == {"chars": 0, "calls": 1}
        assert "show" not in by_tool, "a promotion must not be counted as a fresh show"
