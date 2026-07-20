from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # SQLite-only local installs remain usable.
    psycopg = None
    dict_row = None


DEFAULT_DB_PATH = os.getenv("ROB_DB_PATH", "/tmp/rob_galiciana.sqlite3").strip()


class DatabaseStore:
    """Small shared SQLite/PostgreSQL connection and dialect utility."""

    def __init__(self, path: str = DEFAULT_DB_PATH, database_url: str | None = None) -> None:
        self.path = path or DEFAULT_DB_PATH
        self.database_url = (
            os.getenv("DATABASE_URL", "").strip()
            if database_url is None
            else database_url.strip()
        )
        self.backend = "postgresql" if self.database_url else "sqlite"
        if self.backend == "sqlite":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        elif psycopg is None:
            raise RuntimeError("DATABASE_URL está definida, pero psycopg no está instalado.")
        self._lock = threading.RLock()

    def storage_status(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "persistent": self.backend == "postgresql",
            "configured_by": "DATABASE_URL" if self.backend == "postgresql" else "ROB_DB_PATH",
        }

    def _connect(self) -> Any:
        if self.backend == "postgresql":
            return psycopg.connect(self.database_url, row_factory=dict_row)
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _sql(self, statement: str) -> str:
        if self.backend == "sqlite":
            return statement
        return statement.replace("?", "%s")

    def _execute(
        self, connection: Any, statement: str, params: tuple[Any, ...] = ()
    ) -> Any:
        return connection.execute(self._sql(statement), params)

    def _executemany(self, connection: Any, statement: str, params: Any) -> Any:
        return connection.executemany(self._sql(statement), params)
