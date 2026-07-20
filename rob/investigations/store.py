from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from rob.database import DEFAULT_DB_PATH, DatabaseStore

from .models import InvestigationTarget


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


class UniversalInvestigationStore:
    """Coordinator tables sharing the existing SQLite/PostgreSQL database."""

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

    def storage_status(self) -> dict[str, Any]:
        return self._database.storage_status()

    def _connect(self) -> Any:
        return self._database._connect()

    def _execute(
        self, connection: Any, statement: str, params: tuple[Any, ...] = ()
    ) -> Any:
        return self._database._execute(connection, statement, params)

    def _initialise(self) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS universal_investigations (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL,
                target_json TEXT NOT NULL,
                requested_sources_json TEXT NOT NULL,
                note TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS universal_source_runs (
                id TEXT PRIMARY KEY,
                investigation_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_investigation_id TEXT,
                status TEXT NOT NULL,
                diagnostics_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(investigation_id, source_name),
                FOREIGN KEY (investigation_id)
                    REFERENCES universal_investigations(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_universal_source_runs_investigation
                ON universal_source_runs(investigation_id, status)
            """,
        )
        with self._lock, self._connect() as connection:
            for statement in statements:
                self._execute(connection, statement)

    @staticmethod
    def _decode_investigation(row: Any) -> dict[str, Any]:
        output = dict(row)
        output["target"] = InvestigationTarget.from_dict(
            _loads(output.pop("target_json", None), {})
        )
        output["requested_sources"] = _loads(
            output.pop("requested_sources_json", None), []
        )
        return output

    @staticmethod
    def _decode_run(row: Any) -> dict[str, Any]:
        output = dict(row)
        output["diagnostics"] = _loads(output.pop("diagnostics_json", None), [])
        return output

    def create(
        self,
        target: InvestigationTarget,
        requested_sources: list[str],
        *,
        note: str | None = None,
    ) -> str:
        investigation_id = uuid.uuid4().hex
        now = _utc_now()
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                """
                INSERT INTO universal_investigations(
                    id, created_at, updated_at, status, target_json,
                    requested_sources_json, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    investigation_id,
                    now,
                    now,
                    "pending",
                    _json(target.to_dict()),
                    _json(requested_sources),
                    note,
                ),
            )
        return investigation_id

    def investigation(self, investigation_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = self._execute(
                connection,
                "SELECT * FROM universal_investigations WHERE id=?",
                (investigation_id,),
            ).fetchone()
        return self._decode_investigation(row) if row is not None else None

    def require(self, investigation_id: str) -> dict[str, Any]:
        investigation = self.investigation(investigation_id)
        if investigation is None:
            raise KeyError("La investigación universal no existe.")
        return investigation

    def ensure_source_run(
        self, investigation_id: str, source_name: str
    ) -> dict[str, Any]:
        run_id = uuid.uuid4().hex
        now = _utc_now()
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                """
                INSERT INTO universal_source_runs(
                    id, investigation_id, source_name, source_investigation_id,
                    status, diagnostics_json, created_at, updated_at
                ) VALUES (?, ?, ?, NULL, 'pending', '[]', ?, ?)
                ON CONFLICT(investigation_id, source_name) DO NOTHING
                """,
                (run_id, investigation_id, source_name, now, now),
            )
            row = self._execute(
                connection,
                """
                SELECT * FROM universal_source_runs
                WHERE investigation_id=? AND source_name=?
                """,
                (investigation_id, source_name),
            ).fetchone()
        if row is None:
            raise RuntimeError("No se pudo persistir la ejecución de fuente.")
        return self._decode_run(row)

    def source_runs(self, investigation_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = self._execute(
                connection,
                """
                SELECT * FROM universal_source_runs
                WHERE investigation_id=? ORDER BY created_at, source_name
                """,
                (investigation_id,),
            ).fetchall()
        return [self._decode_run(row) for row in rows]

    def update_run(
        self,
        investigation_id: str,
        source_name: str,
        *,
        status: str,
        source_investigation_id: str | None = None,
        diagnostics: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                """
                UPDATE universal_source_runs
                SET status=?,
                    source_investigation_id=COALESCE(?, source_investigation_id),
                    diagnostics_json=COALESCE(?, diagnostics_json),
                    updated_at=?
                WHERE investigation_id=? AND source_name=?
                """,
                (
                    status,
                    source_investigation_id,
                    _json(diagnostics) if diagnostics is not None else None,
                    now,
                    investigation_id,
                    source_name,
                ),
            )
            row = self._execute(
                connection,
                """
                SELECT * FROM universal_source_runs
                WHERE investigation_id=? AND source_name=?
                """,
                (investigation_id, source_name),
            ).fetchone()
        if row is None:
            raise KeyError("La ejecución de fuente no existe.")
        return self._decode_run(row)

    def update_status(self, investigation_id: str, status: str) -> None:
        with self._lock, self._connect() as connection:
            cursor = self._execute(
                connection,
                """
                UPDATE universal_investigations
                SET status=?, updated_at=? WHERE id=?
                """,
                (status, _utc_now(), investigation_id),
            )
            if cursor.rowcount == 0:
                raise KeyError("La investigación universal no existe.")

    def update_runs(
        self,
        investigation_id: str,
        updates: list[tuple[str, str, list[dict[str, Any]]]],
    ) -> dict[str, dict[str, Any]]:
        """Update several source runs atomically using one database transaction."""
        if not updates:
            return {}
        now = _utc_now()
        names = [source_name for source_name, _, _ in updates]
        with self._lock, self._connect() as connection:
            for source_name, status, diagnostics in updates:
                self._execute(
                    connection,
                    """
                    UPDATE universal_source_runs
                    SET status=?, diagnostics_json=?, updated_at=?
                    WHERE investigation_id=? AND source_name=?
                    """,
                    (
                        status,
                        _json(diagnostics),
                        now,
                        investigation_id,
                        source_name,
                    ),
                )
            rows = self._execute(
                connection,
                "SELECT * FROM universal_source_runs WHERE investigation_id=?",
                (investigation_id,),
            ).fetchall()
        decoded = {
            row["source_name"]: self._decode_run(row)
            for row in rows
            if row["source_name"] in names
        }
        if len(decoded) != len(set(names)):
            raise KeyError("Alguna ejecución de fuente no existe.")
        return decoded
