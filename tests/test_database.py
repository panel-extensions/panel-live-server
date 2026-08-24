"""Tests for display database module."""

import tempfile
from pathlib import Path

import pytest

from panel_live_server.database import Snippet, SnippetDatabase
from panel_live_server.utils import ExtensionError


class TestSnippetDatabase:
    """Tests for SnippetDatabase."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database for testing."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)

        db = SnippetDatabase(db_path)
        yield db

        # Cleanup
        db_path.unlink(missing_ok=True)

    def test_create_snippet(self, temp_db):
        """Test creating a display snippet."""
        snippet = Snippet(
            app="print('hello')",
            name="Test",
            description="Test description",
            method="inline",
        )

        created = temp_db.create_snippet(snippet)

        assert created.id == snippet.id
        assert created.app == "print('hello')"
        assert created.name == "Test"
        assert created.status == "pending"

    def test_get_snippet(self, temp_db):
        """Test retrieving a snippet."""
        snippet = Snippet(
            app="x = 1",
            name="Simple",
            method="inline",
        )

        temp_db.create_snippet(snippet)
        retrieved = temp_db.get_snippet(snippet.id)

        assert retrieved is not None
        assert retrieved.id == snippet.id
        assert retrieved.app == "x = 1"

    def test_get_nonexistent_snippet(self, temp_db):
        """Test getting a snippet that doesn't exist."""
        result = temp_db.get_snippet("nonexistent")
        assert result is None

    def test_update_snippet(self, temp_db):
        """Test updating a snippet."""
        snippet = Snippet(
            app="y = 2",
            method="inline",
        )

        temp_db.create_snippet(snippet)

        # Update status
        updated = temp_db.update_snippet(
            snippet.id,
            status="success",
            execution_time=1.5,
        )

        assert updated is True

        # Retrieve and verify
        retrieved = temp_db.get_snippet(snippet.id)
        assert retrieved.status == "success"
        assert retrieved.execution_time == 1.5

    def test_list_snippets(self, temp_db):
        """Test listing snippets."""
        # Create multiple snippets
        for i in range(5):
            snippet = Snippet(
                app=f"x = {i}",
                name=f"Test {i}",
                method="inline",
            )
            temp_db.create_snippet(snippet)

        # List all
        snippets = temp_db.list_snippets()
        assert len(snippets) == 5

        # List with limit
        snippets = temp_db.list_snippets(limit=3)
        assert len(snippets) == 3

    def test_delete_snippet(self, temp_db):
        """Test deleting a snippet."""
        snippet = Snippet(
            app="z = 3",
            method="inline",
        )

        temp_db.create_snippet(snippet)

        # Delete
        deleted = temp_db.delete_snippet(snippet.id)
        assert deleted is True

        # Verify deleted
        retrieved = temp_db.get_snippet(snippet.id)
        assert retrieved is None

    def test_search_snippets(self, temp_db):
        """Test full-text search."""
        # Create snippets with different content
        snippets = [
            Snippet(app="import pandas", name="Pandas Test", method="inline"),
            Snippet(app="import numpy", name="NumPy Test", method="inline"),
            Snippet(app="import matplotlib", name="Plotting", method="inline"),
        ]

        for snippet in snippets:
            temp_db.create_snippet(snippet)

        # Search for pandas
        results = temp_db.search_snippets("pandas")
        assert len(results) >= 1
        assert any("pandas" in r.app.lower() or "pandas" in r.name.lower() for r in results)

    def test_search_after_delete_does_not_match_the_deleted_snippet(self, temp_db):
        """A deleted snippet must leave nothing behind in the FTS index.

        snippets_fts is an external-content table, which a plain
        `DELETE FROM snippets_fts` does not maintain, so the postings survived the
        delete.
        """
        snippet = Snippet(app="import pandas", name="Pandas Test", method="inline")
        temp_db.create_snippet(snippet)
        assert len(temp_db.search_snippets("pandas")) == 1

        temp_db.delete_snippet(snippet.id)

        assert temp_db.search_snippets("pandas") == []

    def test_search_is_correct_across_rowid_reuse(self, temp_db):
        """A recycled rowid must not inherit the previous occupant's search terms.

        SQLite reuses rowids after a delete. With orphaned FTS postings still
        attached to the old rowid, searching for the *deleted* snippet's terms
        matched whichever snippet inherited it — search_snippets JOINs on rowid,
        so the wrong row came back, not merely an extra one. Every draft
        screenshot was a create/delete pair, so this ran constantly.
        """
        # Create then delete until a rowid gets handed out twice.
        seen: set[int] = set()
        reused = False
        for i in range(20):
            snippet = Snippet(app="import zzsentinelpkg", name=f"Doomed {i}", method="inline")
            temp_db.create_snippet(snippet)
            with temp_db._get_connection() as conn:
                rowid = conn.execute("SELECT rowid FROM snippets WHERE id = ?", (snippet.id,)).fetchone()[0]
            temp_db.delete_snippet(snippet.id)
            if rowid in seen:
                reused = True
                break
            seen.add(rowid)

        assert reused, "expected SQLite to hand back a rowid it had already used"

        # The successor occupies a recycled rowid and shares none of those terms.
        successor = Snippet(app="import numpy", name="Innocent", method="inline")
        temp_db.create_snippet(successor)

        assert temp_db.search_snippets("zzsentinelpkg") == []

        results = temp_db.search_snippets("numpy")
        assert [r.id for r in results] == [successor.id]

    def test_search_reflects_an_updated_indexed_column(self, temp_db):
        """Editing an indexed column must move the row in the index, not duplicate it."""
        snippet = Snippet(app="import pandas", name="Original Title", method="inline")
        temp_db.create_snippet(snippet)

        with temp_db._get_connection() as conn:
            conn.execute("UPDATE snippets SET name = ? WHERE id = ?", ("Renamed Title", snippet.id))
            conn.commit()

        assert temp_db.search_snippets("Original") == []
        assert [r.id for r in temp_db.search_snippets("Renamed")] == [snippet.id]

    def test_status_update_leaves_the_index_alone(self, temp_db):
        """/view stamps status on every page load; that must not reindex the row.

        The update trigger is scoped with `UPDATE OF` to the indexed columns so the
        render hot path does not pay for a delete-and-reinsert each time.
        """
        snippet = Snippet(app="import pandas", name="Pandas Test", method="inline")
        temp_db.create_snippet(snippet)

        for _ in range(3):
            temp_db.update_snippet(snippet.id, status="success", execution_time=0.1)

        assert [r.id for r in temp_db.search_snippets("pandas")] == [snippet.id]

    def test_create_visualization_with_pyodide_method(self, temp_db):
        """Pyodide execution method is accepted and persisted."""
        snippet = temp_db.create_visualization(
            app="print('hello from pyodide')",
            name="Pyodide snippet",
            method="pyodide",
        )

        assert snippet.method == "pyodide"
        assert snippet.status in {"success", "error"}

        persisted = temp_db.get_snippet(snippet.id)
        assert persisted is not None
        assert persisted.method == "pyodide"

    def test_create_visualization_with_invalid_method_raises_value_error(self, temp_db):
        """Invalid execution methods should raise ValueError (for clean 400 mapping)."""
        with pytest.raises(ValueError, match="Unsupported execution method"):
            temp_db.create_visualization(
                app="print('hello')",
                method="invalid",  # type: ignore[arg-type]
            )

    def test_jupyter_method_skips_extension_validation(self, temp_db):
        """Code that triggers extension detection must not raise for method='jupyter'.

        find_extensions() matches on substrings, so a comment containing 'plotly'
        is enough to trigger the extension check without importing the package.
        """
        # This would raise ExtensionError if validate_extension_availability ran for jupyter.
        snippet = temp_db.create_visualization(
            app="x = 1  # plotly visualization",
            method="inline",
        )
        assert snippet.method == "inline"
        assert "plotly" in snippet.extensions

    def test_panel_method_still_enforces_extension_validation(self, temp_db):
        """Same code must raise ExtensionError for method='server'."""
        with pytest.raises(ExtensionError, match="plotly"):
            temp_db.create_visualization(
                app="x = 1  # plotly visualization",
                method="server",
            )
