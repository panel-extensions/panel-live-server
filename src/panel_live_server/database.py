"""Database models and operations for the display server.

This module handles SQLite database operations for storing and retrieving
visualization requests.
"""

import json
import logging
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from panel_live_server.config import get_config
from panel_live_server.utils import find_extensions, find_requirements, validate_code, validate_extension_availability
from panel_live_server.validation import ast_check, check_packages, ruff_check, ruff_format

logger = logging.getLogger(__name__)


class Snippet(BaseModel):
    """Model for a code snippet stored in the database.

    Represents a code snippet submitted to the Display System for visualization.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    app: str = Field(..., description="Python code to execute")
    name: str = Field(default="", description="User-provided name")
    description: str = Field(default="", description="Short description of the app")
    readme: str = Field(default="", description="Longer documentation describing the app")
    method: Literal["inline", "server", "pyodide"] = Field(..., description="Execution method")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: Literal["pending", "success", "error"] = Field(default="pending")
    error_message: Optional[str] = Field(default=None, description="Error details if status='error'")
    execution_time: Optional[float] = Field(default=None, description="Execution time in seconds")
    requirements: list[str] = Field(default_factory=list, description="Inferred required packages")
    extensions: list[str] = Field(default_factory=list, description="Inferred Panel extensions")
    user: str = Field(default="guest", description="User who created the snippet")
    tags: list[str] = Field(default_factory=list, description="List of tags")
    slug: str = Field(default="", description="URL-friendly slug for persistent links")
    draft: bool = Field(default=False, description="Held back from the feed and from search while an agent is still iterating on it")

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        """Validate that slug is either empty or a valid URL slug."""
        if v == "":
            return v
        # Valid slug: lowercase letters, numbers, hyphens only
        if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", v):
            raise ValueError("Slug must be empty or contain only lowercase letters, numbers, and hyphens (no consecutive hyphens)")
        return v


class SnippetDatabase:
    """SQLite database manager for code snippets.

    Manages storage and retrieval of Snippet records (code snippets)
    submitted to the Display System.
    """

    def __init__(self, db_path: Path):
        """Initialize the database.

        Parameters
        ----------
        db_path : Path
            Path to the SQLite database file
        """
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_db()

    def _initialize_db(self) -> None:
        """Create database tables if they don't exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Create main table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS snippets (
                    id TEXT PRIMARY KEY,
                    app TEXT NOT NULL,
                    name TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    readme TEXT DEFAULT '',
                    method TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'pending',
                    error_message TEXT,
                    execution_time REAL,
                    requirements TEXT,
                    extensions TEXT,
                    user TEXT DEFAULT 'guest',
                    tags TEXT,
                    slug TEXT DEFAULT '',
                    draft INTEGER NOT NULL DEFAULT 0
                )
                """
            )

            self._migrate_schema(cursor)

            # Create indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON snippets(created_at DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_status ON snippets(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_method ON snippets(method)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_slug ON snippets(slug)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_user ON snippets(user)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_draft ON snippets(draft)")

            # Create full-text search virtual table
            cursor.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS snippets_fts
                USING fts5(name, description, readme, app, content=snippets)
                """
            )

            self._sync_fts_triggers(cursor)

            conn.commit()

    @staticmethod
    def _migrate_schema(cursor: sqlite3.Cursor) -> None:
        """Add columns introduced after a database may already have been created.

        ``CREATE TABLE IF NOT EXISTS`` is a no-op against an existing database, so
        a new column has to be added explicitly or every query naming it fails on
        the snippets.db someone already has.

        Parameters
        ----------
        cursor : sqlite3.Cursor
            Cursor on an open connection. The caller commits.
        """
        cursor.execute("PRAGMA table_info(snippets)")
        existing = {row[1] for row in cursor.fetchall()}
        if "draft" not in existing:
            cursor.execute("ALTER TABLE snippets ADD COLUMN draft INTEGER NOT NULL DEFAULT 0")

    @staticmethod
    def _sync_fts_triggers(cursor: sqlite3.Cursor) -> None:
        """Install the triggers that keep ``snippets_fts`` in step with ``snippets``.

        ``snippets_fts`` is an external-content FTS5 table (``content=snippets``):
        it stores only the index and reads column values back out of ``snippets``.
        Such a table cannot be maintained with a plain ``DELETE FROM snippets_fts``.
        The documented mechanism is the special ``'delete'`` command, which needs
        the *old* column values in order to unpick the terms it once indexed.

        The plain DELETE this replaces left orphaned postings behind, and because
        SQLite reuses rowids after a delete, those postings would later match
        whichever snippet inherited the rowid — so ``search_snippets`` returned
        rows that did not contain the search term at all. Every draft screenshot
        was a create/delete pair, so the broken path ran constantly.

        Triggers put the bookkeeping next to the writes rather than in the Python
        call sites, which also covers the ``UPDATE`` path for free.

        Parameters
        ----------
        cursor : sqlite3.Cursor
            Cursor on an open connection. The caller commits.
        """
        cursor.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = 'snippets_fts_insert'")
        already_installed = cursor.fetchone() is not None

        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS snippets_fts_insert AFTER INSERT ON snippets BEGIN
                INSERT INTO snippets_fts(rowid, name, description, readme, app)
                VALUES (new.rowid, new.name, new.description, new.readme, new.app);
            END
            """
        )
        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS snippets_fts_delete AFTER DELETE ON snippets BEGIN
                INSERT INTO snippets_fts(snippets_fts, rowid, name, description, readme, app)
                VALUES ('delete', old.rowid, old.name, old.description, old.readme, old.app);
            END
            """
        )
        # Scoped with UPDATE OF so it fires only when an indexed column is being
        # set. Without that, every /view render would reindex the row, since
        # create_view writes status/error_message/execution_time on each load.
        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS snippets_fts_update
            AFTER UPDATE OF name, description, readme, app ON snippets BEGIN
                INSERT INTO snippets_fts(snippets_fts, rowid, name, description, readme, app)
                VALUES ('delete', old.rowid, old.name, old.description, old.readme, old.app);
                INSERT INTO snippets_fts(rowid, name, description, readme, app)
                VALUES (new.rowid, new.name, new.description, new.readme, new.app);
            END
            """
        )

        if not already_installed:
            # Databases written by the old hand-maintained path carry orphaned
            # postings. Rebuild once so the index matches the content table
            # exactly from here on; a no-op cost on a fresh database.
            cursor.execute("INSERT INTO snippets_fts(snippets_fts) VALUES('rebuild')")

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection with context manager."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def create_snippet(self, snippet: Snippet) -> Snippet:
        """Create a new snippet record.

        Parameters
        ----------
        snippet : Snippet
            Snippet record to create

        Returns
        -------
        Snippet
            Created snippet record with ID
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO snippets
                (id, app, name, description, readme, method, created_at, updated_at, status,
                 error_message, execution_time, requirements, extensions, user, tags, slug, draft)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snippet.id,
                    snippet.app,
                    snippet.name,
                    snippet.description,
                    snippet.readme,
                    snippet.method,
                    snippet.created_at.isoformat(),
                    snippet.updated_at.isoformat(),
                    snippet.status,
                    snippet.error_message,
                    snippet.execution_time,
                    json.dumps(snippet.requirements),
                    json.dumps(snippet.extensions),
                    snippet.user,
                    json.dumps(snippet.tags),
                    snippet.slug,
                    int(snippet.draft),
                ),
            )

            # snippets_fts is maintained by trigger (see _sync_fts_triggers).

            conn.commit()

        return snippet

    def get_snippet(self, snippet_id: str) -> Optional[Snippet]:
        """Get a snippet record by ID.

        Parameters
        ----------
        snippet_id : str
            Snippet ID

        Returns
        -------
        Optional[Snippet]
            Snippet record if found, None otherwise
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM snippets WHERE id = ?", (snippet_id,))
            row = cursor.fetchone()

            if row:
                return self._row_to_snippet(dict(row))
            return None

    def get_snippet_by_slug(self, slug: str) -> Optional[Snippet]:
        """Get the most recent snippet record by slug.

        Parameters
        ----------
        slug : str
            Snippet slug

        Returns
        -------
        Optional[Snippet]
            Most recent snippet record with this slug if found, None otherwise
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM snippets WHERE slug = ? ORDER BY created_at DESC LIMIT 1",
                (slug,),
            )
            row = cursor.fetchone()

            if row:
                return self._row_to_snippet(dict(row))
            return None

    def update_snippet(
        self,
        snippet_id: str,
        status: Optional[str] = None,
        error_message: Optional[str] = None,
        execution_time: Optional[float] = None,
        requirements: Optional[list[str]] = None,
        extensions: Optional[list[str]] = None,
        app: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        draft: Optional[bool] = None,
    ) -> bool:
        """Update a snippet record.

        Setting ``app``, ``name`` or ``description`` reindexes the row for search,
        via the ``snippets_fts_update`` trigger. ``status`` and friends do not,
        which matters because every ``/view`` load writes them.

        Parameters
        ----------
        snippet_id : str
            Snippet ID
        status : Optional[str]
            New status
        error_message : Optional[str]
            Error message
        execution_time : Optional[float]
            Execution time
        requirements : Optional[list[str]]
            Required packages
        extensions : Optional[list[str]]
            Required extensions
        app : Optional[str]
            Replacement code
        name : Optional[str]
            Replacement display name
        description : Optional[str]
            Replacement description
        draft : Optional[bool]
            Whether the snippet is still a draft

        Returns
        -------
        bool
            True if updated, False if not found
        """
        updates = []
        params: list[object] = []

        if status is not None:
            updates.append("status = ?")
            params.append(status)

        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)

        if execution_time is not None:
            updates.append("execution_time = ?")
            params.append(execution_time)

        if requirements is not None:
            updates.append("requirements = ?")
            params.append(json.dumps(requirements))

        if extensions is not None:
            updates.append("extensions = ?")
            params.append(json.dumps(extensions))

        if app is not None:
            updates.append("app = ?")
            params.append(app)

        if name is not None:
            updates.append("name = ?")
            params.append(name)

        if description is not None:
            updates.append("description = ?")
            params.append(description)

        if draft is not None:
            updates.append("draft = ?")
            params.append(int(draft))

        if not updates:
            return False

        updates.append("updated_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())

        params.append(snippet_id)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE snippets SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            conn.commit()
            return cursor.rowcount > 0

    def list_snippets(
        self,
        limit: int = 100,
        offset: int = 0,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        status: Optional[str] = None,
        method: Optional[str] = None,
        include_drafts: bool = False,
    ) -> list[Snippet]:
        """List snippet records with filters.

        Parameters
        ----------
        limit : int
            Maximum number of snippets to return
        offset : int
            Number of snippets to skip
        start : Optional[datetime]
            Filter snippets created after this time
        end : Optional[datetime]
            Filter snippets created before this time
        status : Optional[str]
            Filter by status
        method : Optional[str]
            Filter by method
        include_drafts : bool
            Include snippets an agent is still iterating on. Off by default so
            that every existing caller — the feed, the admin page — excludes them
            without having to know they exist.

        Returns
        -------
        list[Snippet]
            List of snippet records
        """
        query = "SELECT * FROM snippets WHERE 1=1"
        params = []

        if not include_drafts:
            query += " AND draft = 0"

        if start:
            query += " AND created_at >= ?"
            params.append(start.isoformat())

        if end:
            query += " AND created_at <= ?"
            params.append(end.isoformat())

        if status:
            query += " AND status = ?"
            params.append(status)

        if method:
            query += " AND method = ?"
            params.append(method)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([str(limit), str(offset)])

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()

            return [self._row_to_snippet(dict(row)) for row in rows]

    def delete_snippet(self, snippet_id: str) -> bool:
        """Delete a snippet record.

        Parameters
        ----------
        snippet_id : str
            Snippet ID

        Returns
        -------
        bool
            True if deleted, False if not found
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # snippets_fts is maintained by trigger (see _sync_fts_triggers). Doing
            # it here with a plain DELETE is what corrupted the index.
            cursor.execute("DELETE FROM snippets WHERE id = ?", (snippet_id,))
            conn.commit()

            return cursor.rowcount > 0

    def search_snippets(self, query: str, limit: int = 100, include_drafts: bool = False) -> list[Snippet]:
        """Search snippet records using full-text search.

        Parameters
        ----------
        query : str
            Search query
        limit : int
            Maximum number of results
        include_drafts : bool
            Include snippets an agent is still iterating on. Off by default, or
            drafts leak to the user through search even while hidden from the feed.

        Returns
        -------
        list[Snippet]
            Matching snippet records
        """
        sql = """
            SELECT r.* FROM snippets r
            JOIN snippets_fts fts ON r.rowid = fts.rowid
            WHERE snippets_fts MATCH ?
        """
        if not include_drafts:
            sql += " AND r.draft = 0"
        sql += " ORDER BY r.created_at DESC LIMIT ?"

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (query, limit))
            rows = cursor.fetchall()

            return [self._row_to_snippet(dict(row)) for row in rows]

    def promote_draft(self, snippet_id: str, name: Optional[str] = None, description: Optional[str] = None) -> Snippet:
        """Turn a draft into a snippet the user can see.

        Promotion deliberately does not re-run the code. The draft already
        rendered under Playwright, which is a strictly stronger check than the
        storage-time execution it would otherwise repeat: a real page load, with
        the real preamble and session extensions, rather than an inline exec.
        Running it again here would reinstate the second execution this whole path
        exists to remove.

        Nothing is reformatted. Stored code stays byte-identical to what the
        caller sent, so a later ``old_str`` edit matches what the author holds;
        formatting is applied when code is *read* for a human instead (the code
        panel and the feed).

        Parameters
        ----------
        snippet_id : str
            Id of the draft to promote
        name : Optional[str]
            Replacement display name. Left as-is when None.
        description : Optional[str]
            Replacement description. Left as-is when None.

        Returns
        -------
        Snippet
            The promoted snippet

        Raises
        ------
        ValueError
            If no such snippet exists, it is not a draft, or its last render
            did not succeed
        """
        snippet = self.get_snippet(snippet_id)
        if snippet is None:
            raise ValueError(f"No draft found with id {snippet_id!r}. Drafts are cleared after a while — screenshot the code again to get a fresh one.")
        if not snippet.draft:
            raise ValueError(f"Snippet {snippet_id!r} is not a draft; it has already been shown to the user.")
        if snippet.status != "success":
            detail = f": {snippet.error_message}" if snippet.error_message else ""
            raise ValueError(f"Draft {snippet_id!r} last rendered with status {snippet.status!r}, so it is not ready to show{detail}")

        self.update_snippet(
            snippet_id,
            name=name,
            description=description,
            draft=False,
        )

        promoted = self.get_snippet(snippet_id)
        if promoted is None:
            raise ValueError(f"Draft {snippet_id!r} disappeared while being promoted")
        return promoted

    def delete_stale_drafts(self, older_than_hours: float) -> int:
        """Delete drafts last touched more than *older_than_hours* ago.

        Drafts are no longer deleted the moment their screenshot is taken, so
        something has to clear them. Age is the simplest rule that works and
        matches how the validation cache is scoped: a draft is only interesting
        while the agent that made it is still working on it.

        Parameters
        ----------
        older_than_hours : float
            Age past which a draft is discarded

        Returns
        -------
        int
            Number of drafts deleted
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM snippets WHERE draft = 1 AND updated_at < ?", (cutoff,))
            conn.commit()
            return cursor.rowcount

    def create_visualization(
        self,
        app: str,
        name: str = "",
        description: str = "",
        readme: str = "",
        method: Literal["inline", "server", "pyodide"] = "inline",
        run_static: bool = True,
        format: bool = True,
        execute: bool = True,
        draft: bool = False,
    ) -> Snippet:
        """Create a visualization request.

        This is the core business logic for creating visualizations,
        shared by both the HTTP API endpoint and the UI form.

        Parameters
        ----------
        app : str
            Python code to execute
        name : str, optional
            Display name for the visualization
        description : str, optional
            Short description of the visualization
        readme : str, optional
            Longer documentation describing the app
        method : str, optional
            Execution method: "inline", "server", or "pyodide"
        run_static : bool, optional
            Run the static layers: syntax, security, package availability, and
            — for ``method="server"`` — Panel extension availability. The MCP
            tools run these themselves before calling, behind a cache, so they
            can turn this off rather than pay for them twice. The web ``/add``
            form leaves it on so untrusted input is still fully checked.
        format : bool, optional
            Autoformat with ``ruff format`` before storing. Off for anything an
            agent may later edit by string match — a reformat between what the
            author holds and what is stored is what makes ``old_str`` miss for
            reasons nobody can see. The web ``/add`` form leaves it on, since a
            human pasting code is not going to string-match against it later.
            Code is formatted when read for display, not on the way in.
        execute : bool, optional
            Run the snippet once here to populate ``status`` and
            ``error_message``. This is what lets ``show`` catch a runtime failure
            *before* the user's iframe loads. The screenshot path turns it off:
            the Playwright render is itself an error detector, so executing here
            as well would run the code twice for one picture. When off, the row
            is stored ``pending`` for the render to settle.
        draft : bool, optional
            Store this as a draft: kept out of the feed, out of search, and swept
            up by age. The screenshot path sets it so an agent can iterate without
            anything reaching the user; ``show(draft_id=...)`` promotes the one it
            settles on.

        Returns
        -------
        Snippet
            The snippet created for the visualization request.

        Raises
        ------
        ValueError
            If app is empty or contains unsupported operations
        SyntaxError
            If app has syntax errors
        Exception
            If database operation or other errors occur
        """
        # Validate app is not empty
        if not app:
            raise ValueError("App code is required")

        supported_methods = {"inline", "server", "pyodide"}
        if method not in supported_methods:
            supported_text = ", ".join(sorted(supported_methods))
            raise ValueError(f"Unsupported execution method '{method}'. Supported methods: {supported_text}")

        if run_static:
            # Layer 1 — Syntax
            if err := ast_check(app):
                raise SyntaxError(err)

            # Layer 2 — Security (raises SecurityError)
            ruff_check(app)

            # Layer 3 — Package availability
            if err := check_packages(app):
                raise ValueError(err)

            # Layer 4 — Panel extension availability (raises ExtensionError)
            # inline method auto-injects extensions at render time; only enforce for server.
            if method == "server":
                validate_extension_availability(app)

        if format:
            # Format before storage and runtime execution
            app = ruff_format(app)

        validation_result = ""
        if execute:
            # Layer 5 — Runtime execution (threaded, stores error but does not block)
            validation_result = validate_code(app)

        # Infer requirements and extensions
        requirements = find_requirements(app)
        extensions = find_extensions(app) if method == "inline" else []

        # Create snippet in database with "pending" status
        snippet_obj = Snippet(
            app=app,
            name=name,
            description=description,
            readme=readme,
            method=method,
            requirements=requirements,
            extensions=extensions,
            # Nothing has run when execute=False, so do not claim success — the
            # render that follows settles it (view_page.create_view stamps the row).
            status=("error" if validation_result else "success") if execute else "pending",
            error_message=validation_result if validation_result else None,
            draft=draft,
        )

        snippet_saved = self.create_snippet(snippet_obj)

        # Return result
        return snippet_saved

    @staticmethod
    def _row_to_snippet(row: dict) -> Snippet:
        """Convert a database row to a Snippet."""
        # Remap legacy method names stored before the rename.
        _method_aliases = {"panel": "server", "jupyter": "inline"}
        method = _method_aliases.get(row["method"], row["method"])
        return Snippet(
            id=row["id"],
            app=row["app"],
            name=row["name"] or "",
            description=row["description"] or "",
            readme=row.get("readme", ""),
            method=method,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            status=row["status"],
            error_message=row["error_message"],
            execution_time=row["execution_time"],
            requirements=json.loads(row["requirements"]) if row["requirements"] else [],
            extensions=json.loads(row["extensions"]) if row["extensions"] else [],
            user=row.get("user", "guest"),
            tags=json.loads(row["tags"]) if row.get("tags") else [],
            slug=row.get("slug", ""),
            draft=bool(row.get("draft", 0)),
        )


# Global database instance cache
_db_instance: Optional[SnippetDatabase] = None


def get_db(db_path: Optional[Path] = None) -> SnippetDatabase:
    """Get or create the SnippetDatabase instance.

    This function implements lazy initialization with a global cache.
    The database instance is created once and reused across the application.

    Parameters
    ----------
    db_path : Optional[Path]
        Path to database file. If None, uses default from environment/config.
        Only used on first call; subsequent calls ignore this parameter.

    Returns
    -------
    SnippetDatabase
        Shared database instance
    """
    global _db_instance

    if _db_instance is None:
        if db_path is None:
            # Try environment variable first
            env_path = os.getenv("DISPLAY_DB_PATH", "")

            if env_path:
                db_path = Path(env_path)
            else:
                # Fall back to default location
                db_path = get_config().db_path

        logger.info(f"Initializing database at: {db_path}")
        _db_instance = SnippetDatabase(db_path)

    return _db_instance


def reset_db() -> None:
    """Reset the database instance.

    This is primarily for testing purposes to ensure a clean state.
    """
    global _db_instance
    _db_instance = None
