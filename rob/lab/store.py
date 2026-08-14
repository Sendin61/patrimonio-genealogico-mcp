from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from .config import resolve_lab_paths


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS investigations (
    id TEXT PRIMARY KEY,
    raw_request TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'created',
    interpretation_json TEXT,
    plan_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS investigation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    investigation_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY(investigation_id) REFERENCES investigations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_events_investigation ON investigation_events(investigation_id, id);

CREATE TABLE IF NOT EXISTS source_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    investigation_id TEXT NOT NULL,
    source TEXT NOT NULL,
    source_key TEXT NOT NULL,
    item_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(investigation_id, source, source_key, item_type),
    FOREIGN KEY(investigation_id) REFERENCES investigations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_source_items_key ON source_items(source, source_key);

CREATE TABLE IF NOT EXISTS ocr_pages (
    source TEXT NOT NULL,
    image_id TEXT NOT NULL,
    ark TEXT,
    dgs TEXT,
    image_number INTEGER,
    raw_ocr TEXT NOT NULL DEFAULT '',
    structured_json TEXT,
    metadata_json TEXT,
    fetched_at REAL NOT NULL,
    PRIMARY KEY(source, image_id)
);
CREATE INDEX IF NOT EXISTS idx_ocr_pages_dgs ON ocr_pages(dgs, image_number);

CREATE VIRTUAL TABLE IF NOT EXISTS ocr_fts USING fts5(
    source UNINDEXED,
    image_id UNINDEXED,
    raw_ocr,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS ocr_resolutions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    investigation_id TEXT NOT NULL,
    source TEXT NOT NULL,
    image_id TEXT NOT NULL,
    raw_ocr TEXT NOT NULL,
    resolved_text TEXT,
    confidence REAL,
    status TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    image_region_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(investigation_id) REFERENCES investigations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS hypotheses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    investigation_id TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    subject TEXT,
    predicate TEXT,
    object TEXT,
    confidence REAL,
    status TEXT NOT NULL DEFAULT 'hypothesis',
    support_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(investigation_id) REFERENCES investigations(id) ON DELETE CASCADE
);
"""


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value: str | None) -> Any:
    if not value:
        return None
    return json.loads(value)


class LabStore:
    def __init__(self, path: Path | None = None) -> None:
        paths = resolve_lab_paths(create=True)
        self.path = path or (paths.data / "rob_genealogy_lab.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialise()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialise(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def create_investigation(self, raw_request: str) -> str:
        investigation_id = "rob_" + uuid.uuid4().hex
        now = time.time()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO investigations(id,raw_request,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                (investigation_id, raw_request, "created", now, now),
            )
        self.add_event(investigation_id, "created", {"raw_request": raw_request})
        return investigation_id

    def update_investigation(
        self,
        investigation_id: str,
        *,
        status: str | None = None,
        interpretation: dict[str, Any] | None = None,
        plan: dict[str, Any] | None = None,
    ) -> None:
        fields: list[str] = ["updated_at=?"]
        values: list[Any] = [time.time()]
        if status is not None:
            fields.append("status=?")
            values.append(status)
        if interpretation is not None:
            fields.append("interpretation_json=?")
            values.append(_dump(interpretation))
        if plan is not None:
            fields.append("plan_json=?")
            values.append(_dump(plan))
        values.append(investigation_id)
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE investigations SET {', '.join(fields)} WHERE id=?",
                values,
            )
            if cursor.rowcount != 1:
                raise KeyError(investigation_id)

    def investigation(self, investigation_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM investigations WHERE id=?", (investigation_id,)
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["interpretation"] = _load(value.pop("interpretation_json"))
        value["plan"] = _load(value.pop("plan_json"))
        return value

    def add_event(self, investigation_id: str, kind: str, payload: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO investigation_events(investigation_id,kind,payload_json,created_at) VALUES(?,?,?,?)",
                (investigation_id, kind, _dump(payload), time.time()),
            )

    def events(self, investigation_id: str, *, after_id: int = 0, limit: int = 250) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id,kind,payload_json,created_at
                FROM investigation_events
                WHERE investigation_id=? AND id>?
                ORDER BY id ASC LIMIT ?
                """,
                (investigation_id, max(0, after_id), max(1, min(limit, 1000))),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            value = dict(row)
            value["payload"] = _load(value.pop("payload_json")) or {}
            output.append(value)
        return output

    def upsert_source_item(
        self,
        investigation_id: str,
        *,
        source: str,
        source_key: str,
        item_type: str,
        payload: dict[str, Any],
    ) -> None:
        now = time.time()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO source_items(investigation_id,source,source_key,item_type,payload_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(investigation_id,source,source_key,item_type)
                DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at
                """,
                (investigation_id, source, source_key, item_type, _dump(payload), now, now),
            )

    def source_item_count(self, investigation_id: str, *, source: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM source_items WHERE investigation_id=?"
        args: list[Any] = [investigation_id]
        if source:
            sql += " AND source=?"
            args.append(source)
        with self.connect() as connection:
            return int(connection.execute(sql, args).fetchone()[0])

    def upsert_ocr_page(
        self,
        *,
        source: str,
        image_id: str,
        raw_ocr: str,
        ark: str | None = None,
        dgs: str | None = None,
        image_number: int | None = None,
        structured: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO ocr_pages(source,image_id,ark,dgs,image_number,raw_ocr,structured_json,metadata_json,fetched_at)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source,image_id) DO UPDATE SET
                    ark=excluded.ark,
                    dgs=excluded.dgs,
                    image_number=excluded.image_number,
                    raw_ocr=excluded.raw_ocr,
                    structured_json=excluded.structured_json,
                    metadata_json=excluded.metadata_json,
                    fetched_at=excluded.fetched_at
                """,
                (
                    source,
                    image_id,
                    ark,
                    dgs,
                    image_number,
                    raw_ocr,
                    _dump(structured) if structured is not None else None,
                    _dump(metadata) if metadata is not None else None,
                    time.time(),
                ),
            )
            connection.execute(
                "DELETE FROM ocr_fts WHERE source=? AND image_id=?", (source, image_id)
            )
            connection.execute(
                "INSERT INTO ocr_fts(source,image_id,raw_ocr) VALUES(?,?,?)",
                (source, image_id, raw_ocr),
            )

    def search_ocr(self, query: str, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT source,image_id,snippet(ocr_fts,2,'[',']',' … ',28) AS snippet, bm25(ocr_fts) AS rank
                FROM ocr_fts WHERE ocr_fts MATCH ? ORDER BY rank LIMIT ?
                """,
                (query, max(1, min(limit, 500))),
            ).fetchall()
        return [dict(row) for row in rows]
