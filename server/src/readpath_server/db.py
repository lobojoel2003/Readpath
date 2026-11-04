# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQLite storage for scans and documents (one file, no server needed)."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    start_url TEXT,
    status TEXT NOT NULL,
    message TEXT,
    pages_crawled INTEGER NOT NULL DEFAULT 0,
    max_pages INTEGER,
    max_depth INTEGER,
    include_subdomains INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    crawl_errors TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    scan_id TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    kind TEXT NOT NULL,
    url TEXT,
    found_on TEXT,
    status TEXT NOT NULL,
    error TEXT,
    size_bytes INTEGER,
    sha256 TEXT,
    page_count INTEGER,
    title TEXT,
    score INTEGER,
    original_score INTEGER,
    grade TEXT,
    counts TEXT,
    report TEXT,
    verapdf TEXT,
    fixes TEXT,
    fixed INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    checked_at TEXT
);
CREATE INDEX IF NOT EXISTS documents_scan ON documents(scan_id);
"""

JSON_FIELDS = {"counts", "report", "verapdf", "fixes", "crawl_errors"}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id() -> str:
    return uuid.uuid4().hex[:16]


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        out = dict(row)
        for key in JSON_FIELDS & out.keys():
            if out[key] is not None:
                try:
                    out[key] = json.loads(out[key])
                except ValueError:
                    out[key] = None
        return out

    def _execute(self, sql: str, params: tuple | list = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)

    def _one(self, sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
        # Fetch inside the lock: the connection is shared between threads
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def _all(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    @staticmethod
    def _encode(values: dict[str, Any]) -> dict[str, Any]:
        return {k: (json.dumps(v) if k in JSON_FIELDS and v is not None else v) for k, v in values.items()}

    def _update(self, table: str, row_id: str, values: dict[str, Any]) -> None:
        if not values:
            return
        values = self._encode(values)
        cols = ", ".join(f"{k} = ?" for k in values)
        self._execute(f"UPDATE {table} SET {cols} WHERE id = ?", [*values.values(), row_id])

    # -- scans ---------------------------------------------------------------
    def create_scan(self, **values: Any) -> dict[str, Any]:
        values.setdefault("id", new_id())
        values.setdefault("created_at", now())
        values = self._encode(values)
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        self._execute(f"INSERT INTO scans ({cols}) VALUES ({marks})", list(values.values()))
        return self.get_scan(values["id"])  # type: ignore[return-value]

    def get_scan(self, scan_id: str) -> dict[str, Any] | None:
        return self._row(self._one("SELECT * FROM scans WHERE id = ?", (scan_id,)))

    def list_scans(self) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM scans ORDER BY created_at DESC")
        return [self._row(r) for r in rows]  # type: ignore[misc]

    def update_scan(self, scan_id: str, **values: Any) -> None:
        self._update("scans", scan_id, values)

    def delete_scan(self, scan_id: str) -> None:
        self._execute("DELETE FROM documents WHERE scan_id = ?", (scan_id,))
        self._execute("DELETE FROM scans WHERE id = ?", (scan_id,))

    def scan_summary(self, scan_id: str) -> dict[str, Any]:
        row = self._one(
            """SELECT COUNT(*) AS documents,
                      SUM(kind = 'pdf') AS pdfs,
                      SUM(kind = 'pdf' AND status = 'checked') AS checked,
                      AVG(CASE WHEN kind = 'pdf' AND status = 'checked' THEN score END) AS average_score,
                      SUM(status = 'checked' AND grade = 'ready') AS ready,
                      SUM(status = 'checked' AND grade = 'auto_fixable') AS auto_fixable,
                      SUM(status = 'checked' AND grade = 'needs_help') AS needs_help,
                      SUM(status = 'error') AS errors,
                      SUM(kind != 'pdf') AS other_documents,
                      SUM(kind = 'pdf' AND status IN ('queued', 'downloading', 'checking', 'fixing')) AS pending
               FROM documents WHERE scan_id = ?""",
            (scan_id,),
        )
        assert row is not None
        out = {k: (row[k] or 0) for k in row.keys()}
        out["average_score"] = round(row["average_score"]) if row["average_score"] is not None else None
        return out

    # -- documents -----------------------------------------------------------
    def create_document(self, **values: Any) -> dict[str, Any]:
        values.setdefault("id", new_id())
        stamp = now()
        values.setdefault("created_at", stamp)
        values.setdefault("updated_at", stamp)
        values = self._encode(values)
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        self._execute(f"INSERT INTO documents ({cols}) VALUES ({marks})", list(values.values()))
        return self.get_document(values["id"])  # type: ignore[return-value]

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        return self._row(self._one("SELECT * FROM documents WHERE id = ?", (doc_id,)))

    def find_document(self, scan_id: str, url: str) -> dict[str, Any] | None:
        return self._row(self._one("SELECT * FROM documents WHERE scan_id = ? AND url = ?",
                                       (scan_id, url)))

    def list_documents(self, scan_id: str) -> list[dict[str, Any]]:
        rows = self._all(
            "SELECT * FROM documents WHERE scan_id = ? ORDER BY (kind != 'pdf'), filename COLLATE NOCASE",
            (scan_id,),
        )
        return [self._row(r) for r in rows]  # type: ignore[misc]

    def documents_with_status(self, statuses: tuple[str, ...]) -> list[dict[str, Any]]:
        marks = ", ".join("?" for _ in statuses)
        rows = self._all(f"SELECT * FROM documents WHERE status IN ({marks})", statuses)
        return [self._row(r) for r in rows]  # type: ignore[misc]

    def update_document(self, doc_id: str, **values: Any) -> None:
        values.setdefault("updated_at", now())
        self._update("documents", doc_id, values)
