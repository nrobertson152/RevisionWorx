# SPDX-License-Identifier: GPL-3.0-or-later
"""Local SQLite search cache.

This database is **disposable and never authoritative**. It lives on the local machine (never on a
share), records the journal sequence it reflects, and can be deleted and rebuilt at any time from the
``.revisionworx`` workspace. Nothing may use it to authorise a checkout or any other write.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from .workspace import Workspace, WorkspaceCorruptError, WorkspaceError, entry_digest

SCHEMA_VERSION = 2  # 2: Phase 2 document/revision/checkout tables
BUSY_TIMEOUT = 15.0  # seconds to wait for another RevisionWorx process using this machine's cache
_BUSY_MESSAGE = "the local search index is in use by another RevisionWorx process; try again in a moment"


class IndexBusyError(WorkspaceError):
    """Another process is using the local index right now. Nothing is wrong with it; try again."""


def _is_busy(exc: sqlite3.Error) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)    # Python 3.11+; the low byte is the primary result code
    if code is not None:
        return code & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    return "locked" in str(exc) or "busy" in str(exc)

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE roots (id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE projects (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, number TEXT NOT NULL, customer TEXT NOT NULL,
    description TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL, root_id TEXT NOT NULL,
    rel_path TEXT NOT NULL, origin TEXT NOT NULL, created TEXT NOT NULL, baseline_blob TEXT);
CREATE INDEX projects_number ON projects(number COLLATE NOCASE);
CREATE INDEX projects_customer ON projects(customer COLLATE NOCASE);
CREATE INDEX projects_name ON projects(name COLLATE NOCASE);
CREATE VIRTUAL TABLE projects_fts USING fts5(id UNINDEXED, name, number, customer, description,
                                             tokenize = 'unicode61 remove_diacritics 2');
CREATE TABLE files (
    project_id TEXT NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
    category TEXT NOT NULL, sha256 TEXT, verified_format TEXT, PRIMARY KEY (project_id, path))
    WITHOUT ROWID;
CREATE INDEX files_category ON files(category);
CREATE INDEX files_sha ON files(sha256) WHERE sha256 IS NOT NULL;
CREATE VIRTUAL TABLE files_fts USING fts5(project_id UNINDEXED, path,
                                          tokenize = "unicode61 separators '/._-'");
CREATE TABLE documents (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, rel_path TEXT NOT NULL, type_id TEXT NOT NULL,
    number TEXT NOT NULL, title TEXT NOT NULL, owner TEXT NOT NULL, status TEXT NOT NULL,
    latest_rev_id TEXT, released_rev_id TEXT, rev_count INTEGER NOT NULL);
CREATE INDEX documents_project ON documents(project_id);
CREATE INDEX documents_number ON documents(number COLLATE NOCASE);
CREATE VIRTUAL TABLE documents_fts USING fts5(id UNINDEXED, number, title, rel_path,
                                              tokenize = "unicode61 separators '/._-'");
CREATE TABLE revisions (
    id TEXT PRIMARY KEY, document_id TEXT NOT NULL, number INTEGER NOT NULL, label TEXT NOT NULL,
    state TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, author TEXT NOT NULL,
    created TEXT NOT NULL, description TEXT NOT NULL);
CREATE INDEX revisions_document ON revisions(document_id, number);
CREATE INDEX revisions_sha ON revisions(sha256);
CREATE TABLE checkouts (document_id TEXT PRIMARY KEY, owner_user TEXT NOT NULL, owner_host TEXT NOT NULL,
                        acquired TEXT NOT NULL);
"""

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def default_cache_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "RevisionWorx" / "cache"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "revisionworx"


def fts_query(text: str) -> str | None:
    """Turn free user text into a safe FTS5 prefix query (no operator injection)."""
    tokens = _TOKEN_RE.findall(text)
    if not tokens:
        return None
    return " ".join(f'"{t}"*' for t in tokens[:16])


class LocalIndex:
    def __init__(self, ws: Workspace, cache_dir: Path | None = None):
        self.ws = ws
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.cache_dir / f"{ws.id}.sqlite"
        self.db = self._open()

    # ------------------------------------------------------------------ setup
    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=BUSY_TIMEOUT)

    def _discard_files(self) -> None:
        try:
            for suffix in ("", "-wal", "-shm"):
                Path(str(self.path) + suffix).unlink(missing_ok=True)
        except PermissionError as exc:           # Windows: another process has it open
            raise IndexBusyError(_BUSY_MESSAGE) from exc

    def _usable(self, db: sqlite3.Connection) -> bool | None:
        """True: this workspace's cache. False: someone else's, an old format or damaged, so disposable.
        None: an empty database, nobody's yet. Raises IndexBusyError: in use, so it is not ours to judge."""
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone() is None:
                return None
            row = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            wsrow = db.execute("SELECT value FROM meta WHERE key='workspace_id'").fetchone()
            return row is not None and int(row[0]) == SCHEMA_VERSION and wsrow is not None and wsrow[0] == self.ws.id
        except sqlite3.DatabaseError as exc:
            if _is_busy(exc):
                raise IndexBusyError(_BUSY_MESSAGE) from exc
            return False

    def _usable_when_free(self, db: sqlite3.Connection) -> bool | None:
        """SQLite does not wait on its busy timeout when switching a new database to WAL: racing openers fail at
        once (seen on real Windows). Being briefly in use is not a reason to give up, so wait here instead."""
        deadline = time.monotonic() + BUSY_TIMEOUT
        while True:
            try:
                return self._usable(db)
            except IndexBusyError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)

    def _open(self) -> sqlite3.Connection:
        db = self._connect()
        try:
            usable = self._usable_when_free(db)
            if usable is False:
                db.close()
                self._discard_files()
                db = self._connect()
                usable = self._usable_when_free(db)
            if usable is None:
                self._create_schema(db)
        except BaseException:
            db.close()
            raise
        return db

    def _create_schema(self, db: sqlite3.Connection) -> None:
        """All or nothing, and safe to race: whoever comes second finds it done."""
        workspace_id = self.ws.id.replace("'", "''")
        try:
            db.executescript(
                f"BEGIN IMMEDIATE;{SCHEMA}"
                f"INSERT INTO meta VALUES ('schema_version', '{SCHEMA_VERSION}'), "
                f"('workspace_id', '{workspace_id}'), ('journal_seq', '0');COMMIT;")
        except sqlite3.OperationalError as exc:
            db.rollback()
            if _is_busy(exc):
                raise IndexBusyError(_BUSY_MESSAGE) from exc
            if "already exists" not in str(exc):
                raise

    def _start_over(self) -> None:
        """The cache is damaged. It holds nothing the journal does not, so replace it."""
        self.db.close()
        self._discard_files()
        self.db = self._open()
        self.rebuild()

    def _healing(self, work: Callable[[], Any]) -> Any:
        """Run a query. If the cache turns out to be damaged, replace it and run the query once more."""
        try:
            return work()
        except sqlite3.DatabaseError as exc:
            if _is_busy(exc):
                raise IndexBusyError(_BUSY_MESSAGE) from exc
            self._start_over()
            return work()

    def close(self) -> None:
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ sync
    def cursor_seq(self) -> int:
        return int(self._meta("journal_seq"))

    def _meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))

    def _digest_at(self, seq: int) -> str | None:
        """The journal's own fingerprint of an entry. None if it cannot be read (missing or damaged)."""
        if seq == 0:
            return ""
        try:
            entry = self.ws.read_entry(seq)
        except (OSError, WorkspaceCorruptError):
            return None
        return entry.get("digest") or entry_digest(entry)

    def _anchored(self, cur: int) -> bool:
        """Is this the journal the cache was built from? A cursor alone cannot tell: a restored workspace
        can grow back to or past it with different entries, and the cache would then serve records that
        exist in no journal. Entry digests cover the txid and time, so they differ across timelines.

        A damaged entry cannot answer, and the workspace is already recovery-blocked then: searching stays
        available, as reads do, and a repair brings the entry and this check back."""
        try:
            entry = self.ws.read_entry(cur) if cur else None
        except WorkspaceCorruptError:
            return True
        except OSError:
            return False
        return self._meta("journal_digest") == ((entry.get("digest") or entry_digest(entry)) if entry else "")

    def status(self) -> dict:
        head = self.ws.head_seq()
        cur = self.cursor_seq()
        return {"cache_path": str(self.path), "cache_seq": cur, "workspace_seq": head,
                "stale": cur != head, "authoritative": False}

    def _upsert_project(self, rec: dict) -> None:
        pid = rec["id"]
        self.db.execute("DELETE FROM projects WHERE id=?", (pid,))
        self.db.execute("DELETE FROM projects_fts WHERE id=?", (pid,))
        baseline = (rec.get("baseline") or {}).get("blob")
        self.db.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, rec["name"], rec.get("number", ""), rec.get("customer", ""),
             rec.get("description", ""), rec["mode"], rec["status"], rec["root_id"], rec["rel_path"],
             rec.get("origin", ""), rec.get("created", ""), baseline))
        self.db.execute("INSERT INTO projects_fts VALUES (?,?,?,?,?)",
                        (pid, rec["name"], rec.get("number", ""), rec.get("customer", ""),
                         rec.get("description", "")))

    def _load_files(self, pid: str, blob_ref: str | None) -> None:
        self.db.execute("DELETE FROM files WHERE project_id=?", (pid,))
        self.db.execute("DELETE FROM files_fts WHERE project_id=?", (pid,))
        if not blob_ref:
            return
        try:
            baseline = self.ws.read_blob(blob_ref)
        except (OSError, ValueError):            # verify() reports this; the rest of the index still works
            self._note_baseline(pid, unavailable=True)
            return
        self._note_baseline(pid, unavailable=False)
        rows = [(pid, f["path"], f["size"], f["mtime_ns"], f["category"], f.get("sha256"),
                 f.get("verified_format")) for f in baseline["files"]]
        self.db.executemany("INSERT INTO files VALUES (?,?,?,?,?,?,?)", rows)
        self.db.executemany("INSERT INTO files_fts VALUES (?,?)", [(pid, r[1]) for r in rows])

    def _note_baseline(self, pid: str, unavailable: bool) -> None:
        ids = set(json.loads(self._meta("baselines_unavailable") or "[]"))
        (ids.add if unavailable else ids.discard)(pid)
        self._set_meta("baselines_unavailable", json.dumps(sorted(ids)))

    def _apply_put(self, kind: str, rid: str, data: dict) -> None:
        if kind == "project":
            old = self.db.execute("SELECT baseline_blob FROM projects WHERE id=?", (rid,)).fetchone()
            self._upsert_project(data)
            new_blob = (data.get("baseline") or {}).get("blob")
            if old is None or old[0] != new_blob:
                self._load_files(rid, new_blob)
        elif kind == "root":
            self.db.execute("INSERT OR REPLACE INTO roots VALUES (?,?)", (rid, data["name"]))
        elif kind == "document":
            self.db.execute("DELETE FROM documents_fts WHERE id=?", (rid,))
            self.db.execute("INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (rid, data["project_id"], data["rel_path"], data["type_id"], data.get("number", ""),
                             data.get("title", ""), data.get("owner", ""), data["status"],
                             data.get("latest_rev_id"), data.get("released_rev_id"), data.get("rev_count", 0)))
            self.db.execute("INSERT INTO documents_fts VALUES (?,?,?,?)",
                            (rid, data.get("number", ""), data.get("title", ""), data["rel_path"]))
        elif kind == "revision":
            self.db.execute("INSERT OR REPLACE INTO revisions VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (rid, data["document_id"], data["number"], data["label"], data["state"],
                             data["sha256"], data["size"], data["author"].get("user", ""), data["created"],
                             data["description"]))
        elif kind == "checkout":
            self.db.execute("INSERT OR REPLACE INTO checkouts VALUES (?,?,?,?)",
                            (rid, data["owner"].get("user", ""), data["owner"].get("host", ""), data["acquired"]))

    def _apply_delete(self, kind: str, rid: str) -> None:
        if kind == "project":
            for table, col in (("projects", "id"), ("projects_fts", "id"), ("files", "project_id"),
                               ("files_fts", "project_id")):
                self.db.execute(f"DELETE FROM {table} WHERE {col}=?", (rid,))
        elif kind == "root":
            self.db.execute("DELETE FROM roots WHERE id=?", (rid,))
        elif kind == "checkout":
            self.db.execute("DELETE FROM checkouts WHERE document_id=?", (rid,))
        elif kind == "document":
            self.db.execute("DELETE FROM documents WHERE id=?", (rid,))
            self.db.execute("DELETE FROM documents_fts WHERE id=?", (rid,))
        elif kind == "revision":
            self.db.execute("DELETE FROM revisions WHERE id=?", (rid,))

    def rebuild(self) -> int:
        """Discard everything and reload from one stable snapshot of the authoritative workspace.

        Issue #9: the journal head and the record listing must describe the same state. The head is
        sampled before and after listing all kinds; if anything was committed in between, the
        listing is retried, so the stored cursor never claims less than the cache contains."""
        kinds = ("root", "project", "document", "revision", "checkout")
        for attempt in range(50):
            head = self.ws.head_seq()
            snapshot = {k: self.ws.list(k) for k in kinds}
            if self.ws.head_seq() == head:
                break
            time.sleep(0.01 * min(attempt + 1, 10))
        else:
            raise WorkspaceError("the workspace changed continuously during index rebuild; try again")
        with self.db:
            for table in ("roots", "projects", "projects_fts", "files", "files_fts", "documents",
                          "documents_fts", "revisions", "checkouts"):
                self.db.execute(f"DELETE FROM {table}")
            self._set_meta("baselines_unavailable", "[]")
            for kind in kinds:
                for rid, data in snapshot[kind].items():
                    self._apply_put(kind, rid, data)
            self._set_meta("journal_seq", str(head))
            self._set_meta("journal_digest", self._digest_at(head) or "")
        return head

    def sync(self) -> dict:
        """Apply journal entries newer than the cache. Rebuilds if continuity cannot be proven, and replaces
        the cache if it turns out to be damaged."""
        try:
            return self._sync()
        except sqlite3.DatabaseError as exc:
            if _is_busy(exc):
                raise IndexBusyError(_BUSY_MESSAGE) from exc
            self._start_over()
            return {"mode": "rebuild", "reason": "the local index was damaged and has been replaced"}

    def _sync(self) -> dict:
        cur = self.cursor_seq()
        head = self.ws.head_seq()
        if cur > head:
            self.rebuild()
            return {"mode": "rebuild", "reason": "cache is ahead of workspace (restored backup?)"}
        if not self._anchored(cur):
            self.rebuild()
            return {"mode": "rebuild", "reason": "the journal is not the one this cache was built from"}
        if cur == head:
            return {"mode": "current", "applied": 0}
        applied = 0
        expected = cur + 1
        with self.db:
            for entry in self.ws.entries_after(cur):
                if entry["seq"] != expected:
                    break
                for item in entry["records"]:
                    if item.get("delete"):
                        self._apply_delete(item["kind"], item["id"])
                    else:
                        self._apply_put(item["kind"], item["id"], item["data"])
                self._set_meta("journal_seq", str(entry["seq"]))
                self._set_meta("journal_digest", entry.get("digest") or entry_digest(entry))
                expected += 1
                applied += 1
        if self.cursor_seq() != head:
            self.rebuild()
            return {"mode": "rebuild", "reason": "missed journal entries detected"}
        return {"mode": "incremental", "applied": applied}

    # ------------------------------------------------------------------ queries
    _PROJECT_COLS = "id, name, number, customer, description, mode, status, root_id, rel_path, origin, created"

    def _rows(self, sql: str, args: tuple) -> list[dict]:
        def run() -> list[dict]:
            cur = self.db.execute(sql, args)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        return self._healing(run)

    def project_by_number(self, number: str) -> list[dict]:
        return self._rows(f"SELECT {self._PROJECT_COLS} FROM projects WHERE number = ? COLLATE NOCASE",
                          (number,))

    def search_projects(self, text: str = "", include_archived: bool = False, limit: int = 100,
                        offset: int = 0) -> list[dict]:
        status_clause = "" if include_archived else "AND p.status = 'active'"
        q = fts_query(text)
        if q is None:
            return self._rows(
                f"SELECT {self._PROJECT_COLS} FROM projects p WHERE 1=1 {status_clause} "
                "ORDER BY p.name COLLATE NOCASE LIMIT ? OFFSET ?", (limit, offset))
        cols = ", ".join(f"p.{c.strip()}" for c in self._PROJECT_COLS.split(","))
        return self._rows(
            f"SELECT {cols} FROM projects_fts f JOIN projects p ON p.id = f.id "
            f"WHERE projects_fts MATCH ? {status_clause} ORDER BY bm25(projects_fts), p.name "
            "LIMIT ? OFFSET ?", (q, limit, offset))

    def count_projects(self, include_archived: bool = False) -> int:
        clause = "" if include_archived else "WHERE status = 'active'"
        return self._rows(f"SELECT COUNT(*) AS n FROM projects {clause}", ())[0]["n"]

    def search_files(self, text: str, project_id: str | None = None, limit: int = 100,
                     offset: int = 0) -> list[dict]:
        """Search inventoried files of registered projects only (never unmanaged folders)."""
        q = fts_query(text)
        if q is None:
            return []
        proj = "AND f.project_id = ?" if project_id else ""
        args: tuple = (q, project_id, limit, offset) if project_id else (q, limit, offset)
        return self._rows(
            "SELECT fl.project_id, fl.path, fl.size, fl.category, fl.sha256, fl.verified_format "
            "FROM files_fts f JOIN files fl ON fl.project_id = f.project_id AND fl.path = f.path "
            f"WHERE files_fts MATCH ? {proj} ORDER BY fl.path LIMIT ? OFFSET ?", args)

    def project_files(self, project_id: str, limit: int = 1000, offset: int = 0) -> list[dict]:
        """A project's inventoried files (Claude B's A5), so callers need not query ``idx.db`` directly."""
        return self._rows("SELECT project_id, path, size, category, sha256, verified_format FROM files "
                          "WHERE project_id = ? ORDER BY path LIMIT ? OFFSET ?", (project_id, limit, offset))

    def documents(self, project_id: str | None = None, text: str = "", limit: int = 200,
                  offset: int = 0) -> list[dict]:
        """Documents with their latest/released revision labels and checkout owner."""
        cols = ("d.id, d.project_id, d.rel_path, d.type_id, d.number, d.title, d.owner, d.status, "
                "d.rev_count, lr.label AS latest_label, lr.state AS latest_state, rr.label AS released_label, "
                "c.owner_user AS checked_out_by, c.owner_host AS checked_out_host")
        joins = ("LEFT JOIN revisions lr ON lr.id = d.latest_rev_id LEFT JOIN revisions rr ON rr.id = d.released_rev_id "
                 "LEFT JOIN checkouts c ON c.document_id = d.id")
        where, args = [], []
        if project_id:
            where.append("d.project_id = ?")
            args.append(project_id)
        q = fts_query(text)
        if q:
            where.append("d.id IN (SELECT id FROM documents_fts WHERE documents_fts MATCH ?)")
            args.append(q)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        return self._rows(f"SELECT {cols} FROM documents d {joins} {clause} "
                          "ORDER BY d.rel_path COLLATE NOCASE LIMIT ? OFFSET ?", (*args, limit, offset))

    def revisions(self, document_id: str) -> list[dict]:
        return self._rows("SELECT id, number, label, state, sha256, size, author, created, description "
                          "FROM revisions WHERE document_id = ? ORDER BY number", (document_id,))

    def documents_by_content(self, sha256: str) -> list[dict]:
        return self._rows("SELECT r.document_id, r.id AS revision_id, r.label FROM revisions r WHERE r.sha256 = ?",
                          (sha256,))

    def coverage(self) -> dict:
        """What the index covers, so callers can never imply company-wide completeness."""
        rows = self._rows("SELECT mode, COUNT(*) AS n FROM projects GROUP BY mode", ())
        return {"registered_projects": {r["mode"]: r["n"] for r in rows},
                "files_inventoried": self._rows("SELECT COUNT(*) AS n FROM files", ())[0]["n"],
                "baselines_unavailable": json.loads(self._healing(lambda: self._meta("baselines_unavailable")) or "[]"),
                "note": "Only registered (indexed/managed) projects are covered. Unmanaged folders "
                        "are never searched."}


def open_index(ws: Workspace, cache_dir: Path | None = None) -> LocalIndex:
    idx = LocalIndex(ws, cache_dir)
    try:
        idx.sync()
    except BaseException:
        idx.close()
        raise
    return idx

