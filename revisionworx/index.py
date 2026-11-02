# SPDX-License-Identifier: GPL-3.0-or-later
"""Local SQLite search cache.

This database is **disposable and never authoritative**. It lives on the local machine (never on a
share), records the journal sequence it reflects, and can be deleted and rebuilt at any time from the
``.revisionworx`` workspace. Nothing may use it to authorise a checkout or any other write.
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

from .workspace import Workspace

SCHEMA_VERSION = 2  # 2: Phase 2 document/revision/checkout tables

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
    def _open(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            row = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            ok = row is not None and int(row[0]) == SCHEMA_VERSION
            wsrow = db.execute("SELECT value FROM meta WHERE key='workspace_id'").fetchone()
            ok = ok and wsrow is not None and wsrow[0] == self.ws.id
        except sqlite3.DatabaseError:
            ok = False
        if not ok:
            db.close()
            for suffix in ("", "-wal", "-shm"):
                Path(str(self.path) + suffix).unlink(missing_ok=True)
            db = sqlite3.connect(self.path)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            with db:
                db.executescript(SCHEMA)
                db.executemany("INSERT INTO meta VALUES (?, ?)",
                               [("schema_version", str(SCHEMA_VERSION)), ("workspace_id", self.ws.id),
                                ("journal_seq", "0")])
        return db

    def close(self) -> None:
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ sync
    def cursor_seq(self) -> int:
        return int(self.db.execute("SELECT value FROM meta WHERE key='journal_seq'").fetchone()[0])

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
        baseline = self.ws.read_blob(blob_ref)
        rows = [(pid, f["path"], f["size"], f["mtime_ns"], f["category"], f.get("sha256"),
                 f.get("verified_format")) for f in baseline["files"]]
        self.db.executemany("INSERT INTO files VALUES (?,?,?,?,?,?,?)", rows)
        self.db.executemany("INSERT INTO files_fts VALUES (?,?)", [(pid, r[1]) for r in rows])

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
        """Discard everything and reload from the authoritative workspace."""
        head = self.ws.head_seq()  # captured first: later entries are replayed idempotently
        with self.db:
            for table in ("roots", "projects", "projects_fts", "files", "files_fts", "documents",
                          "documents_fts", "revisions", "checkouts"):
                self.db.execute(f"DELETE FROM {table}")
            for kind in ("root", "project", "document", "revision", "checkout"):
                for rid, data in self.ws.list(kind).items():
                    self._apply_put(kind, rid, data)
            self.db.execute("UPDATE meta SET value=? WHERE key='journal_seq'", (str(head),))
        return head

    def sync(self) -> dict:
        """Apply journal entries newer than the cache. Rebuilds if continuity cannot be proven."""
        cur = self.cursor_seq()
        head = self.ws.head_seq()
        if cur > head:
            self.rebuild()
            return {"mode": "rebuild", "reason": "cache is ahead of workspace (restored backup?)"}
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
                self.db.execute("UPDATE meta SET value=? WHERE key='journal_seq'", (str(entry["seq"]),))
                expected += 1
                applied += 1
        if self.cursor_seq() != head:
            self.rebuild()
            return {"mode": "rebuild", "reason": "missed journal entries detected"}
        return {"mode": "incremental", "applied": applied}

    # ------------------------------------------------------------------ queries
    _PROJECT_COLS = "id, name, number, customer, description, mode, status, root_id, rel_path, origin, created"

    def _rows(self, sql: str, args: tuple) -> list[dict]:
        cur = self.db.execute(sql, args)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

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
        return self.db.execute(f"SELECT COUNT(*) FROM projects {clause}").fetchone()[0]

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
        rows = self.db.execute("SELECT mode, COUNT(*) FROM projects GROUP BY mode").fetchall()
        return {"registered_projects": dict(rows),
                "files_inventoried": self.db.execute("SELECT COUNT(*) FROM files").fetchone()[0],
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

