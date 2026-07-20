from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from rob.database import DEFAULT_DB_PATH, DatabaseStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


class ExaInvestigationStore:
    def __init__(
        self,
        path: str = DEFAULT_DB_PATH,
        database_url: str | None = None,
        *,
        database: DatabaseStore | None = None,
    ) -> None:
        self._database = database or DatabaseStore(path, database_url=database_url)
        self.backend = self._database.backend
        self._lock = threading.RLock()
        self._initialise()

    def _connect(self) -> Any:
        return self._database._connect()

    def _execute(self, connection: Any, statement: str, params: tuple[Any, ...] = ()) -> Any:
        return self._database._execute(connection, statement, params)

    def _initialise(self) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS exa_investigations (
                id TEXT PRIMARY KEY, target_json TEXT NOT NULL, status TEXT NOT NULL,
                queries_json TEXT NOT NULL, diagnostics_json TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS exa_results (
                id TEXT PRIMARY KEY, investigation_id TEXT NOT NULL,
                result_order INTEGER NOT NULL, query_text TEXT NOT NULL,
                url TEXT NOT NULL, canonical_url TEXT NOT NULL, title TEXT,
                author TEXT, published_date TEXT, image_url TEXT, score REAL,
                status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT, highlights_json TEXT NOT NULL DEFAULT '[]',
                recovered_text TEXT, read_at TEXT,
                UNIQUE(investigation_id, canonical_url),
                FOREIGN KEY (investigation_id) REFERENCES exa_investigations(id)
                    ON DELETE CASCADE
            )
            """,
            """CREATE INDEX IF NOT EXISTS idx_exa_results_work
               ON exa_results(investigation_id, status, result_order)""",
        )
        with self._lock, self._connect() as connection:
            for statement in statements:
                self._execute(connection, statement)

    @staticmethod
    def _investigation(row: Any) -> dict[str, Any]:
        output = dict(row)
        output["target"] = _loads(output.pop("target_json", None), {})
        output["queries"] = _loads(output.pop("queries_json", None), [])
        output["diagnostics"] = _loads(output.pop("diagnostics_json", None), [])
        return output

    @staticmethod
    def _result(row: Any) -> dict[str, Any]:
        output = dict(row)
        output["highlights"] = _loads(output.pop("highlights_json", None), [])
        return output

    def create(self, target: dict[str, Any]) -> str:
        investigation_id = uuid.uuid4().hex
        now = _now()
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                """INSERT INTO exa_investigations
                   (id,target_json,status,queries_json,diagnostics_json,created_at,updated_at)
                   VALUES (?,?,'processing','[]','[]',?,?)""",
                (investigation_id, _json(target), now, now),
            )
        return investigation_id

    def require(self, investigation_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            row = self._execute(
                connection, "SELECT * FROM exa_investigations WHERE id=?", (investigation_id,)
            ).fetchone()
        if row is None:
            raise KeyError("La investigación Exa no existe.")
        return self._investigation(row)

    def finish_search(
        self, investigation_id: str, *, status: str, queries: list[str], diagnostics: list[dict[str, Any]]
    ) -> None:
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                """UPDATE exa_investigations SET status=?,queries_json=?,diagnostics_json=?,updated_at=?
                   WHERE id=?""",
                (status, _json(queries), _json(diagnostics), _now(), investigation_id),
            )

    def append_diagnostic(self, investigation_id: str, diagnostic: dict[str, Any]) -> None:
        investigation = self.require(investigation_id)
        self.finish_search(
            investigation_id,
            status=investigation["status"],
            queries=investigation["queries"],
            diagnostics=[*investigation["diagnostics"], diagnostic],
        )

    def add_candidate(self, investigation_id: str, order: int, query: str, item: dict[str, Any], canonical_url: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = self._execute(
                connection,
                """INSERT INTO exa_results
                   (id,investigation_id,result_order,query_text,url,canonical_url,title,
                    author,published_date,image_url,score,status,attempts,highlights_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending',0,'[]')
                   ON CONFLICT(investigation_id,canonical_url) DO NOTHING""",
                (uuid.uuid4().hex, investigation_id, order, query, item["url"], canonical_url,
                 item.get("title"), item.get("author"), item.get("publishedDate"),
                 item.get("image"), item.get("score")),
            )
        return cursor.rowcount > 0

    def claim(self, investigation_id: str, limit: int) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            suffix = " FOR UPDATE SKIP LOCKED" if self.backend == "postgresql" else ""
            rows = self._execute(
                connection,
                """SELECT * FROM exa_results WHERE investigation_id=?
                   AND status IN ('pending','retryable') ORDER BY result_order LIMIT ?""" + suffix,
                (investigation_id, max(1, limit)),
            ).fetchall()
            claimed_rows = []
            for row in rows:
                cursor = self._execute(
                    connection,
                    """UPDATE exa_results SET status='reading',attempts=attempts+1
                       WHERE id=? AND status IN ('pending','retryable')""",
                    (row["id"],),
                )
                if cursor.rowcount:
                    claimed_rows.append(row)
        output = []
        for row in claimed_rows:
            item = self._result(row)
            item["attempts"] += 1
            item["status"] = "reading"
            output.append(item)
        return output

    def mark(self, result_id: str, *, status: str, error: str = "", data: dict[str, Any] | None = None) -> None:
        data = data or {}
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                """UPDATE exa_results SET status=?,error=?,title=COALESCE(?,title),
                   author=COALESCE(?,author),published_date=COALESCE(?,published_date),
                   image_url=COALESCE(?,image_url),score=COALESCE(?,score),
                   highlights_json=?,recovered_text=?,read_at=? WHERE id=?""",
                (status, error[:500], data.get("title") or None, data.get("author") or None,
                 data.get("publishedDate") or None, data.get("image") or None,
                 data.get("score"), _json(data.get("highlights", [])),
                 str(data.get("text") or "")[:8000], _now() if status == "read" else None,
                 result_id),
            )

    def stats(self, investigation_id: str) -> dict[str, int]:
        with self._lock, self._connect() as connection:
            rows = self._execute(
                connection,
                "SELECT status,COUNT(*) AS amount FROM exa_results WHERE investigation_id=? GROUP BY status",
                (investigation_id,),
            ).fetchall()
        counts = {str(row["status"]): int(row["amount"]) for row in rows}
        return {name: counts.get(name, 0) for name in ("pending", "reading", "read", "retryable", "failed")}

    def rows(self, investigation_id: str, offset: int, limit: int) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = self._execute(
                connection,
                "SELECT * FROM exa_results WHERE investigation_id=? ORDER BY result_order LIMIT ? OFFSET ?",
                (investigation_id, max(1, limit), max(0, offset)),
            ).fetchall()
        return [self._result(row) for row in rows]
