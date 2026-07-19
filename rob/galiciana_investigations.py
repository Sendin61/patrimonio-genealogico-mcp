from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

import httpx

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # SQLite-only local installs remain usable.
    psycopg = None
    dict_row = None

from .connectors.galiciana_ocr import (
    BASE_URL,
    DEFAULT_HEADERS,
    SEARCH_REFERER,
    GalicianaOCRConnector,
    _clean_xml_payload,
    _extract_alto_text,
    _is_antibot_page,
    _resolve_antibot,
)
from .models import GenealogyQuery


DEFAULT_DB_PATH = os.getenv("ROB_DB_PATH", "/tmp/rob_galiciana.sqlite3").strip()
DEFAULT_TTL_DAYS = max(1, int(os.getenv("ROB_INVESTIGATION_TTL_DAYS", "14")))


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


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _normalise(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", (value or "").casefold())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _compact(without_accents)


def _page_number(page_label: str | None) -> int | None:
    if not page_label:
        return None
    match = re.search(r"\b(\d{1,4})\b", page_label)
    return int(match.group(1)) if match else None


def _mention_key(mention: Any) -> str:
    record = str(getattr(mention, "record_id", "") or getattr(mention, "title", ""))
    image = str(getattr(mention, "image_id", "") or getattr(mention, "page_url", ""))
    return f"{record}::{image}"


def _query_to_dict(query: GenealogyQuery) -> dict[str, Any]:
    return {
        "name": query.name,
        "variants": list(query.variants),
        "places": list(query.places),
        "year_from": query.year_from,
        "year_to": query.year_to,
        "spouse": query.spouse,
        "profession": query.profession,
    }


def _dict_to_query(value: dict[str, Any]) -> GenealogyQuery:
    return GenealogyQuery(
        name=str(value.get("name") or "").strip(),
        variants=[str(item).strip() for item in value.get("variants", []) if str(item).strip()],
        places=[str(item).strip() for item in value.get("places", []) if str(item).strip()],
        year_from=value.get("year_from"),
        year_to=value.get("year_to"),
        spouse=str(value.get("spouse") or "").strip() or None,
        profession=str(value.get("profession") or "").strip() or None,
    )


def _safe_name(value: str) -> str:
    value = _compact(value).strip(" ,;:.()[]{}")
    value = re.sub(r"^(?:don|doña|d\.?|dª|sr\.?|sra\.?)\s+", "", value, flags=re.I)
    value = re.sub(r"\s+(?:q\.?e\.?p\.?d\.?|fallecido|finado)$", "", value, flags=re.I)
    if len(value) < 5 or len(value) > 100:
        return ""
    words = value.split()
    if len(words) < 2 or len(words) > 7:
        return ""
    forbidden = {
        "ayuntamiento", "alcalde", "comercio", "provincia", "parroquia",
        "juzgado", "secretaria", "corporacion", "establecimiento", "familia",
    }
    if any(_normalise(word) in forbidden for word in words):
        return ""
    return value


class InvestigationStore:
    """Persistent dossier backed by PostgreSQL, with SQLite as a local fallback."""

    def __init__(self, path: str = DEFAULT_DB_PATH, database_url: str | None = None) -> None:
        self.path = path or DEFAULT_DB_PATH
        self.database_url = (
            os.getenv("DATABASE_URL", "").strip() if database_url is None else database_url.strip()
        )
        self.backend = "postgresql" if self.database_url else "sqlite"
        if self.backend == "sqlite":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        elif psycopg is None:
            raise RuntimeError("DATABASE_URL está definida, pero psycopg no está instalado.")
        self._lock = threading.RLock()
        self._initialise()
        self.prune(DEFAULT_TTL_DAYS)

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

    def _initialise(self) -> None:
        common_schema = """
        CREATE TABLE IF NOT EXISTS investigations (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            status TEXT NOT NULL,
            query_json TEXT NOT NULL,
            queries_json TEXT NOT NULL,
            diagnostics_json TEXT NOT NULL,
            search_status TEXT NOT NULL,
            note TEXT,
            total_mentions INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS mentions (
            investigation_id TEXT NOT NULL,
            mention_key TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            record_id TEXT,
            title TEXT NOT NULL,
            publication TEXT,
            document_date TEXT,
            page_label TEXT,
            page_number INTEGER,
            record_url TEXT,
            page_url TEXT NOT NULL,
            path TEXT,
            image_id TEXT,
            snippets_json TEXT NOT NULL,
            matched_query TEXT,
            score REAL NOT NULL DEFAULT 0,
            score_reasons_json TEXT NOT NULL,
            categories_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            ocr_url TEXT,
            image_url TEXT,
            full_text TEXT,
            context_text TEXT,
            layout_json TEXT,
            continuation_before INTEGER NOT NULL DEFAULT 0,
            continuation_after INTEGER NOT NULL DEFAULT 0,
            adjacent_before_text TEXT,
            adjacent_after_text TEXT,
            error TEXT,
            processed_at TEXT,
            PRIMARY KEY (investigation_id, mention_key),
            FOREIGN KEY (investigation_id) REFERENCES investigations(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_mentions_pending
            ON mentions(investigation_id, status, score DESC, ordinal ASC);
        CREATE INDEX IF NOT EXISTS idx_mentions_path
            ON mentions(investigation_id, path, page_number);

        CREATE TABLE IF NOT EXISTS page_cache (
            cache_key TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            page_number INTEGER NOT NULL,
            ocr_url TEXT,
            image_url TEXT,
            full_text TEXT NOT NULL,
            layout_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        """
        relations_schema = """
        CREATE TABLE IF NOT EXISTS relations (
            id {relation_id},
            investigation_id TEXT NOT NULL,
            mention_key TEXT NOT NULL,
            subject_name TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            relative_name TEXT NOT NULL,
            evidence TEXT NOT NULL,
            confidence REAL NOT NULL,
            source_url TEXT,
            UNIQUE(investigation_id, mention_key, relation_type, relative_name, evidence),
            FOREIGN KEY (investigation_id) REFERENCES investigations(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_relations_investigation
            ON relations(investigation_id, relation_type, relative_name);
        """
        with self._lock, self._connect() as connection:
            if self.backend == "sqlite":
                connection.executescript(
                    common_schema + relations_schema.format(
                        relation_id="INTEGER PRIMARY KEY AUTOINCREMENT"
                    )
                )
            else:
                for statement in (common_schema + relations_schema.format(
                    relation_id="BIGSERIAL PRIMARY KEY"
                )).split(";"):
                    if statement.strip():
                        connection.execute(statement)

    def _sql(self, statement: str) -> str:
        if self.backend == "sqlite":
            return statement
        return statement.replace("?", "%s")

    def _execute(self, connection: Any, statement: str, params: tuple[Any, ...] = ()) -> Any:
        return connection.execute(self._sql(statement), params)

    def _executemany(self, connection: Any, statement: str, params: Any) -> Any:
        return connection.executemany(self._sql(statement), params)

    def prune(self, ttl_days: int) -> None:
        threshold = time.time() - ttl_days * 86400
        with self._lock, self._connect() as connection:
            rows = self._execute(connection, "SELECT id, updated_at FROM investigations").fetchall()
            stale: list[str] = []
            for row in rows:
                try:
                    stamp = datetime.fromisoformat(row["updated_at"]).timestamp()
                except (TypeError, ValueError):
                    continue
                if stamp < threshold:
                    stale.append(row["id"])
            self._executemany(connection, "DELETE FROM investigations WHERE id=?", [(item,) for item in stale])

    def create(self, query: GenealogyQuery, report: Any) -> str:
        investigation_id = uuid.uuid4().hex
        now = _utc_now()
        with self._lock, self._connect() as connection:
            self._execute(connection,
                """
                INSERT INTO investigations(
                    id, created_at, updated_at, status, query_json, queries_json,
                    diagnostics_json, search_status, note, total_mentions
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    investigation_id,
                    now,
                    now,
                    "pending" if report.mentions else "complete",
                    _json(_query_to_dict(query)),
                    _json(list(report.queries)),
                    _json([asdict(item) for item in report.diagnostics]),
                    str(report.status),
                    str(report.note),
                    len(report.mentions),
                ),
            )
            for ordinal, mention in enumerate(report.mentions):
                categories = [item.category for item in mention.interpretations]
                self._execute(connection,
                    """
                    INSERT INTO mentions(
                        investigation_id, mention_key, ordinal, record_id, title,
                        publication, document_date, page_label, page_number,
                        record_url, page_url, path, image_id, snippets_json,
                        matched_query, score, score_reasons_json, categories_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        investigation_id,
                        _mention_key(mention),
                        ordinal,
                        mention.record_id,
                        mention.title,
                        mention.parent_publication,
                        mention.date,
                        mention.page,
                        _page_number(mention.page),
                        mention.record_url,
                        mention.page_url,
                        mention.path,
                        mention.image_id,
                        _json(mention.snippets),
                        mention.matched_query,
                        float(mention.score),
                        _json(mention.score_reasons),
                        _json(categories),
                    ),
                )
        return investigation_id

    def investigation(self, investigation_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = self._execute(connection,
                "SELECT * FROM investigations WHERE id=?", (investigation_id,)
            ).fetchone()
            return dict(row) if row else None

    def query(self, investigation_id: str) -> GenealogyQuery:
        row = self.investigation(investigation_id)
        if row is None:
            raise KeyError("La investigación no existe o ya caducó.")
        return _dict_to_query(_loads(row["query_json"], {}))

    def reset_reading(self, investigation_id: str) -> None:
        """Recover pages left in reading state by an interrupted Action call."""
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                """UPDATE mentions SET status='retryable'
                   WHERE investigation_id=? AND status='reading'
                     AND (processed_at IS NULL OR processed_at < ?)""",
                (investigation_id, datetime.fromtimestamp(time.time() - 600, timezone.utc).isoformat()),
            )

    def pending(self, investigation_id: str, limit: int) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            statement = """
                SELECT * FROM mentions
                WHERE investigation_id=? AND status IN ('pending','retryable')
                ORDER BY score DESC, COALESCE(path,''), COALESCE(page_number,9999), ordinal
                LIMIT ?
                """
            if self.backend == "postgresql":
                statement += " FOR UPDATE SKIP LOCKED"
            rows = self._execute(connection, statement,
                (investigation_id, max(1, limit)),
            ).fetchall()
            claimed = [dict(row) for row in rows]
            for row in claimed:
                self._execute(
                    connection,
                    """UPDATE mentions SET status='reading', attempts=attempts+1,
                              error=NULL, processed_at=?
                       WHERE investigation_id=? AND mention_key=?""",
                    (_utc_now(), investigation_id, row["mention_key"]),
                )
            return claimed

    def mark_reading(self, investigation_id: str, mention_key: str) -> None:
        with self._lock, self._connect() as connection:
            self._execute(connection,
                """
                UPDATE mentions SET status='reading', attempts=attempts+1, error=NULL
                WHERE investigation_id=? AND mention_key=?
                """,
                (investigation_id, mention_key),
            )

    def mark_result(
        self,
        investigation_id: str,
        mention_key: str,
        *,
        status: str,
        ocr_url: str | None = None,
        image_url: str | None = None,
        full_text: str = "",
        context_text: str = "",
        layout: list[dict[str, Any]] | None = None,
        continuation_before: bool = False,
        continuation_after: bool = False,
        adjacent_before_text: str = "",
        adjacent_after_text: str = "",
        error: str | None = None,
    ) -> None:
        with self._lock, self._connect() as connection:
            self._execute(connection,
                """
                UPDATE mentions SET
                    status=?, ocr_url=?, image_url=?, full_text=?, context_text=?,
                    layout_json=?, continuation_before=?, continuation_after=?,
                    adjacent_before_text=?, adjacent_after_text=?, error=?, processed_at=?
                WHERE investigation_id=? AND mention_key=?
                """,
                (
                    status,
                    ocr_url,
                    image_url,
                    full_text,
                    context_text,
                    _json(layout or []),
                    int(continuation_before),
                    int(continuation_after),
                    adjacent_before_text,
                    adjacent_after_text,
                    error,
                    _utc_now(),
                    investigation_id,
                    mention_key,
                ),
            )
            self._refresh_status(connection, investigation_id)

    def _refresh_status(self, connection: sqlite3.Connection, investigation_id: str) -> None:
        counts = self._execute(connection,
            """
            SELECT
                SUM(CASE WHEN status='read' THEN 1 ELSE 0 END) AS read_count,
                SUM(CASE WHEN status IN ('failed','retryable') THEN 1 ELSE 0 END) AS failed_count,
                SUM(CASE WHEN status IN ('pending','reading','retryable') THEN 1 ELSE 0 END) AS open_count,
                COUNT(*) AS total_count
            FROM mentions WHERE investigation_id=?
            """,
            (investigation_id,),
        ).fetchone()
        status = "complete" if int(counts["open_count"] or 0) == 0 else "processing"
        self._execute(connection,
            "UPDATE investigations SET status=?, updated_at=? WHERE id=?",
            (status, _utc_now(), investigation_id),
        )

    def cache_get(self, path: str, page_number: int) -> dict[str, Any] | None:
        key = f"{path}:{page_number}"
        with self._lock, self._connect() as connection:
            row = self._execute(connection, "SELECT * FROM page_cache WHERE cache_key=?", (key,)).fetchone()
            return dict(row) if row else None

    def cache_put(
        self,
        *,
        path: str,
        page_number: int,
        ocr_url: str,
        image_url: str | None,
        full_text: str,
        layout: list[dict[str, Any]],
    ) -> None:
        key = f"{path}:{page_number}"
        with self._lock, self._connect() as connection:
            self._execute(connection,
                """
                INSERT INTO page_cache(cache_key,path,page_number,ocr_url,image_url,full_text,layout_json,updated_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    ocr_url=excluded.ocr_url,
                    image_url=COALESCE(excluded.image_url,page_cache.image_url),
                    full_text=excluded.full_text,
                    layout_json=excluded.layout_json,
                    updated_at=excluded.updated_at
                """,
                (key, path, page_number, ocr_url, image_url, full_text, _json(layout), _utc_now()),
            )

    def add_relations(
        self,
        investigation_id: str,
        mention_key: str,
        subject_name: str,
        source_url: str,
        relations: Iterable[dict[str, Any]],
    ) -> None:
        with self._lock, self._connect() as connection:
            for relation in relations:
                try:
                    statement = """
                        INSERT INTO relations(
                            investigation_id, mention_key, subject_name, relation_type,
                            relative_name, evidence, confidence, source_url
                        ) VALUES(?,?,?,?,?,?,?,?)
                        ON CONFLICT(investigation_id, mention_key, relation_type, relative_name, evidence)
                        DO NOTHING
                        """
                    self._execute(connection,
                        statement,
                        (
                            investigation_id,
                            mention_key,
                            subject_name,
                            relation["relation_type"],
                            relation["relative_name"],
                            relation["evidence"],
                            float(relation["confidence"]),
                            source_url,
                        ),
                    )
                except (KeyError, TypeError, ValueError):
                    continue

    def summary(self, investigation_id: str) -> dict[str, Any]:
        row = self.investigation(investigation_id)
        if row is None:
            raise KeyError("La investigación no existe o ya caducó.")
        with self._lock, self._connect() as connection:
            counts = self._execute(connection,
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status='read' THEN 1 ELSE 0 END) AS read_count,
                    SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending_count,
                    SUM(CASE WHEN status='reading' THEN 1 ELSE 0 END) AS reading_count,
                    SUM(CASE WHEN status='retryable' THEN 1 ELSE 0 END) AS retryable_count,
                    SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed_count
                FROM mentions WHERE investigation_id=?
                """,
                (investigation_id,),
            ).fetchone()
        total = int(counts["total"] or 0)
        read_count = int(counts["read_count"] or 0)
        return {
            "investigation_id": investigation_id,
            "estado": row["status"],
            "estado_busqueda": row["search_status"],
            "total_resultados": total,
            "paginas_leidas": read_count,
            "paginas_pendientes": int(counts["pending_count"] or 0),
            "paginas_leyendo": int(counts["reading_count"] or 0),
            "paginas_reintentables": int(counts["retryable_count"] or 0),
            "paginas_fallidas": int(counts["failed_count"] or 0),
            "cobertura": round(read_count / total, 3) if total else 1.0,
            "consultas": _loads(row["queries_json"], []),
            "actualizada": row["updated_at"],
        }

    def report_rows(
        self, investigation_id: str, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = self._execute(connection,
                """
                SELECT * FROM mentions WHERE investigation_id=?
                ORDER BY COALESCE(document_date,'9999'), ordinal LIMIT ? OFFSET ?
                """,
                (investigation_id, max(1, limit), max(0, offset)),
            ).fetchall()
            return [dict(row) for row in rows]

    def relations(self, investigation_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            evidence_aggregate = (
                "STRING_AGG(evidence, ' || ')" if self.backend == "postgresql"
                else "GROUP_CONCAT(evidence, ' || ')"
            )
            sources_aggregate = (
                "STRING_AGG(source_url, ' || ')" if self.backend == "postgresql"
                else "GROUP_CONCAT(source_url, ' || ')"
            )
            rows = self._execute(connection,
                f"""
                SELECT relation_type, relative_name, MAX(confidence) AS confidence,
                       COUNT(*) AS evidence_count,
                       {evidence_aggregate} AS evidence,
                       {sources_aggregate} AS source_urls
                FROM relations WHERE investigation_id=?
                GROUP BY relation_type, relative_name
                ORDER BY confidence DESC, evidence_count DESC, relative_name
                """,
                (investigation_id,),
            ).fetchall()
            return [dict(row) for row in rows]


class GalicianaInvestigationEngine:
    def __init__(
        self,
        *,
        store: InvestigationStore | None = None,
        db_path: str = DEFAULT_DB_PATH,
        timeout: float = 40.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.store = store or InvestigationStore(db_path)
        self.timeout = timeout
        self.transport = transport
        self._investigation_locks: dict[str, asyncio.Lock] = {}
        self._investigation_locks_guard = threading.Lock()

    def _lock_for(self, investigation_id: str) -> asyncio.Lock:
        with self._investigation_locks_guard:
            lock = self._investigation_locks.get(investigation_id)
            if lock is None:
                lock = asyncio.Lock()
                self._investigation_locks[investigation_id] = lock
            return lock

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            transport=self.transport,
            headers=DEFAULT_HEADERS,
        )

    async def create_investigation(
        self,
        query: GenealogyQuery,
        *,
        maximum_queries: int = 8,
        maximum_results: int = 80,
    ) -> dict[str, Any]:
        connector = GalicianaOCRConnector(timeout=self.timeout, transport=self.transport)
        report = await connector.investigate(
            query,
            maximum_queries=max(1, min(maximum_queries, 10)),
            maximum_results=max(1, min(maximum_results, 150)),
            read_full_pages=False,
        )
        investigation_id = self.store.create(query, report)
        summary = self.store.summary(investigation_id)
        summary.update(
            {
                "nombre": query.name,
                "variantes": list(query.variants),
                "lugares": list(query.places),
                "siguiente_paso": (
                    "Llama a procesarInvestigacionGaliciana hasta que completada sea true."
                    if report.mentions
                    else "No hay páginas pendientes. Revisa las variantes o amplía el intervalo."
                ),
            }
        )
        return summary

    async def _seed(self, client: httpx.AsyncClient) -> None:
        for url in (f"{BASE_URL}/es/inicio/inicio.do", SEARCH_REFERER):
            try:
                response = await client.get(url)
                response.raise_for_status()
                if _is_antibot_page(response.text):
                    await _resolve_antibot(client, response)
            except httpx.HTTPError:
                continue

    @staticmethod
    def _alto_url(path: str, page_number: int, language: str = "gl") -> str:
        query = urlencode({"path": path, "posicion": page_number})
        return f"{BASE_URL}/{language}/catalogo_imagenes/descargarAlto.do?{query}"

    @staticmethod
    def _image_url(image_id: str | None) -> str | None:
        if not image_id:
            return None
        return f"{BASE_URL}/gl/catalogo_imagenes/imagen_id.do?idImagen={image_id}"

    async def _fetch_alto(
        self,
        client: httpx.AsyncClient,
        *,
        path: str,
        page_number: int,
        referer: str,
        image_id: str | None,
        retries: int = 3,
    ) -> dict[str, Any]:
        cached = self.store.cache_get(path, page_number)
        if cached:
            return {
                "ok": True,
                "cached": True,
                "ocr_url": cached["ocr_url"],
                "image_url": cached["image_url"] or self._image_url(image_id),
                "text": cached["full_text"],
                "layout": _loads(cached["layout_json"], []),
            }

        last_error = ""
        for language in ("gl", "es"):
            url = self._alto_url(path, page_number, language)
            for attempt in range(1, max(1, retries) + 1):
                try:
                    response = await client.get(url, headers={"Referer": referer})
                    response.raise_for_status()
                    response, _ = await _resolve_antibot(client, response)
                    text = _extract_alto_text(response.text)
                    if not text.strip():
                        raise ValueError("El endpoint ALTO no devolvió texto legible.")
                    layout = extract_alto_layout(response.text)
                    image_url = self._image_url(image_id)
                    self.store.cache_put(
                        path=path,
                        page_number=page_number,
                        ocr_url=str(response.url),
                        image_url=image_url,
                        full_text=text,
                        layout=layout,
                    )
                    return {
                        "ok": True,
                        "cached": False,
                        "ocr_url": str(response.url),
                        "image_url": image_url,
                        "text": text,
                        "layout": layout,
                    }
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {str(exc).strip() or repr(exc)}"
                    if attempt < retries:
                        await asyncio.sleep(min(3.0, 0.8 * (2 ** (attempt - 1))))
        return {"ok": False, "error": last_error or "No se pudo recuperar el ALTO."}

    async def process(
        self,
        investigation_id: str,
        *,
        batch_size: int = 5,
        time_budget_seconds: int = 40,
        read_adjacent: bool = True,
    ) -> dict[str, Any]:
        async with self._lock_for(investigation_id):
            return await self._process_locked(
                investigation_id,
                batch_size=batch_size,
                time_budget_seconds=time_budget_seconds,
                read_adjacent=read_adjacent,
            )

    async def _process_locked(
        self,
        investigation_id: str,
        *,
        batch_size: int,
        time_budget_seconds: int,
        read_adjacent: bool,
    ) -> dict[str, Any]:
        query = self.store.query(investigation_id)
        self.store.reset_reading(investigation_id)
        pending = self.store.pending(investigation_id, max(1, min(batch_size, 8)))
        if not pending:
            summary = self.store.summary(investigation_id)
            summary.update({"procesadas_en_esta_llamada": 0, "completada": True})
            return summary

        started = time.monotonic()
        processed = 0
        succeeded = 0
        failed = 0
        cache_hits = 0
        details: list[dict[str, Any]] = []

        async with self._client() as client:
            await self._seed(client)
            for mention in pending:
                if processed and time.monotonic() - started >= max(10, time_budget_seconds):
                    break
                processed += 1
                key = mention["mention_key"]
                path = str(mention.get("path") or "")
                page_number = mention.get("page_number")
                if not path or not page_number:
                    self.store.mark_result(
                        investigation_id,
                        key,
                        status="failed",
                        image_url=self._image_url(mention.get("image_id")),
                        error="Faltan path o número de página para construir el ALTO directo.",
                    )
                    failed += 1
                    continue

                result = await self._fetch_alto(
                    client,
                    path=path,
                    page_number=int(page_number),
                    referer=mention["page_url"],
                    image_id=mention.get("image_id"),
                )
                if not result.get("ok"):
                    status = "retryable" if int(mention.get("attempts") or 0) < 2 else "failed"
                    self.store.mark_result(
                        investigation_id,
                        key,
                        status=status,
                        image_url=self._image_url(mention.get("image_id")),
                        error=str(result.get("error") or "Error remoto sin detalle."),
                    )
                    failed += 1
                    details.append({"pagina": mention["page_label"], "estado": status})
                    continue

                if result.get("cached"):
                    cache_hits += 1
                context = extract_article_context(
                    result["layout"],
                    result["text"],
                    [query.name, *query.variants],
                )
                before_text = ""
                after_text = ""

                if read_adjacent and context["continuation_before"] and int(page_number) > 1:
                    adjacent = await self._fetch_alto(
                        client,
                        path=path,
                        page_number=int(page_number) - 1,
                        referer=mention["page_url"],
                        image_id=None,
                        retries=2,
                    )
                    if adjacent.get("ok"):
                        before_text = _tail_for_continuation(adjacent["text"])

                if read_adjacent and context["continuation_after"]:
                    adjacent = await self._fetch_alto(
                        client,
                        path=path,
                        page_number=int(page_number) + 1,
                        referer=mention["page_url"],
                        image_id=None,
                        retries=2,
                    )
                    if adjacent.get("ok"):
                        after_text = _head_for_continuation(adjacent["text"])

                combined_context = "\n".join(
                    part for part in (before_text, context["context"], after_text) if part.strip()
                ).strip()
                relations = extract_family_relations(combined_context, query.name)
                self.store.mark_result(
                    investigation_id,
                    key,
                    status="read",
                    ocr_url=result["ocr_url"],
                    image_url=result["image_url"],
                    full_text=result["text"],
                    context_text=combined_context,
                    layout=result["layout"],
                    continuation_before=context["continuation_before"],
                    continuation_after=context["continuation_after"],
                    adjacent_before_text=before_text,
                    adjacent_after_text=after_text,
                )
                self.store.add_relations(
                    investigation_id,
                    key,
                    query.name,
                    mention["page_url"],
                    relations,
                )
                succeeded += 1
                details.append(
                    {
                        "fecha": mention["document_date"],
                        "pagina": mention["page_label"],
                        "estado": "read",
                        "contexto_caracteres": len(combined_context),
                        "relaciones_familiares": len(relations),
                        "cache": bool(result.get("cached")),
                    }
                )

        summary = self.store.summary(investigation_id)
        completed = (
            summary["paginas_pendientes"] == 0
            and summary["paginas_leyendo"] == 0
            and summary["paginas_reintentables"] == 0
        )
        summary.update(
            {
                "procesadas_en_esta_llamada": processed,
                "leidas_en_esta_llamada": succeeded,
                "fallidas_en_esta_llamada": failed,
                "cache_hits": cache_hits,
                "completada": completed,
                "detalle": details,
                "siguiente_paso": (
                    "Llama otra vez a procesarInvestigacionGaliciana."
                    if not completed
                    else "Llama a obtenerInformeGaliciana."
                ),
            }
        )
        return summary

    def report(
        self,
        investigation_id: str,
        *,
        maximum_results: int = 20,
        offset: int = 0,
        include_unread: bool = True,
    ) -> dict[str, Any]:
        summary = self.store.summary(investigation_id)
        query = self.store.query(investigation_id)
        maximum_results = max(1, min(maximum_results, 40))
        offset = max(0, offset)
        rows = self.store.report_rows(investigation_id, maximum_results, offset)
        evidence: list[dict[str, Any]] = []
        unread: list[dict[str, Any]] = []
        for row in rows:
            base = {
                "fecha": row["document_date"],
                "publicacion": row["publication"],
                "titulo": row["title"],
                "pagina": row["page_label"],
                "puntuacion": row["score"],
                "categorias": _loads(row["categories_json"], []),
                "url_pagina": row["page_url"],
                "url_ocr": row["ocr_url"],
                "url_imagen": row["image_url"] or self._image_url(row["image_id"]),
            }
            if row["status"] == "read":
                base.update(
                    {
                        "fuente_texto": "ALTO XML completo",
                        "contexto": row["context_text"],
                        "continuacion_anterior_revisada": bool(row["adjacent_before_text"]),
                        "continuacion_posterior_revisada": bool(row["adjacent_after_text"]),
                    }
                )
                evidence.append(base)
            elif include_unread:
                base.update(
                    {
                        "estado_lectura": row["status"],
                        "fragmentos_busqueda": _loads(row["snippets_json"], []),
                        "error": row["error"],
                    }
                )
                unread.append(base)

        relations = self.store.relations(investigation_id)
        return {
            **summary,
            "persona_objetivo": query.name,
            "evidencias_documentales": evidence,
            "paginas_no_leidas": unread,
            "familia_documentada_o_candidata": relations,
            "paginacion": {
                "desde": offset,
                "devueltos": len(rows),
                "siguiente_desde": (
                    offset + len(rows)
                    if offset + len(rows) < summary["total_resultados"]
                    else None
                ),
            },
            "reglas_de_lectura": {
                "texto_literal": "contexto procedente del ALTO, sin corregir silenciosamente",
                "familia": "solo relaciones explícitas extraídas del texto",
                "homonimos": "los datos aportados se usan para puntuar, no como descubrimientos",
            },
        }

    async def create_family_investigation(
        self,
        parent_investigation_id: str,
        relative_name: str,
        *,
        maximum_results: int = 40,
    ) -> dict[str, Any]:
        relative_name = _safe_name(relative_name)
        if not relative_name:
            raise ValueError("El nombre del familiar no parece suficientemente específico.")
        parent = self.store.query(parent_investigation_id)
        relations = self.store.relations(parent_investigation_id)
        explicit = any(
            _normalise(item["relative_name"]) == _normalise(relative_name)
            and float(item["confidence"] or 0) >= 0.75
            for item in relations
        )
        if not explicit:
            raise ValueError(
                "Ese nombre no figura todavía como relación familiar explícita en las páginas leídas."
            )
        query = GenealogyQuery(
            name=relative_name,
            variants=[],
            places=list(parent.places),
            year_from=parent.year_from,
            year_to=parent.year_to,
            spouse=None,
            profession=None,
        )
        output = await self.create_investigation(
            query,
            maximum_queries=6,
            maximum_results=max(1, min(maximum_results, 80)),
        )
        output["investigacion_origen"] = parent_investigation_id
        output["persona_ancla"] = parent.name
        output["relacion_previamente_documentada"] = True
        return output


def extract_alto_layout(xml_text: str) -> list[dict[str, Any]]:
    """Keep ALTO geometry so ROB can reconstruct columns and article context."""
    try:
        root = ET.fromstring(_clean_xml_payload(xml_text, root_name="alto"))
    except ET.ParseError:
        return []

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].casefold()

    page_width: int | None = None
    page_height: int | None = None
    for element in root.iter():
        if local(element.tag) != "page":
            continue
        try:
            page_width = int(float(element.attrib.get("WIDTH", "")))
        except (TypeError, ValueError):
            page_width = None
        try:
            page_height = int(float(element.attrib.get("HEIGHT", "")))
        except (TypeError, ValueError):
            page_height = None
        break

    output: list[dict[str, Any]] = []
    block_index = -1
    for block in root.iter():
        if local(block.tag) != "textblock":
            continue
        block_index += 1
        for line in block.iter():
            if local(line.tag) != "textline":
                continue
            words: list[str] = []
            for item in line.iter():
                if local(item.tag) == "string":
                    content = item.attrib.get("CONTENT") or item.attrib.get("content")
                    if content:
                        words.append(content)
            text = _compact(" ".join(words))
            if not text:
                continue

            def number(name: str) -> int | None:
                raw = line.attrib.get(name) or line.attrib.get(name.casefold())
                try:
                    return int(float(raw)) if raw is not None else None
                except (TypeError, ValueError):
                    return None

            output.append(
                {
                    "text": text,
                    "block": block_index,
                    "x": number("HPOS"),
                    "y": number("VPOS"),
                    "width": number("WIDTH"),
                    "height": number("HEIGHT"),
                    "page_width": page_width,
                    "page_height": page_height,
                }
            )
    return output


def _line_matches(lines: list[dict[str, Any]], index: int, terms: list[str]) -> bool:
    window = " ".join(
        str(lines[position].get("text") or "")
        for position in range(max(0, index - 2), min(len(lines), index + 3))
    )
    normalized = _normalise(window)
    normalized_terms = [_normalise(term) for term in terms if _compact(term)]
    if any(term and term in normalized for term in normalized_terms):
        return True
    canonical_tokens = [token for token in re.findall(r"[a-z0-9]+", normalized_terms[0] if normalized_terms else "") if len(token) >= 2]
    return bool(canonical_tokens) and all(token in normalized for token in canonical_tokens)


def _looks_like_heading(text: str) -> bool:
    compact = _compact(text)
    letters = [char for char in compact if char.isalpha()]
    if not letters or len(compact) > 140:
        return False
    uppercase_ratio = sum(char.isupper() for char in letters) / len(letters)
    return uppercase_ratio >= 0.72 or compact.endswith(":")


def extract_article_context(
    layout: list[dict[str, Any]],
    full_text: str,
    terms: list[str],
    *,
    maximum_characters: int = 3500,
) -> dict[str, Any]:
    """Heuristic article window, not a false claim of perfect article segmentation."""
    if not layout:
        normalized = _normalise(full_text)
        hit = -1
        for term in terms:
            hit = normalized.find(_normalise(term))
            if hit >= 0:
                break
        if hit < 0:
            context = full_text[:maximum_characters]
        else:
            context = full_text[max(0, hit - 1600) : hit + 3000]
        return {
            "context": context.strip(),
            "continuation_before": False,
            "continuation_after": False,
            "method": "text-window",
        }

    ordered = sorted(
        layout,
        key=lambda item: (
            item.get("y") if item.get("y") is not None else 10**9,
            item.get("x") if item.get("x") is not None else 10**9,
            item.get("block", 0),
        ),
    )
    hits = [index for index in range(len(ordered)) if _line_matches(ordered, index, terms)]
    if not hits:
        return {
            "context": full_text[:maximum_characters].strip(),
            "continuation_before": False,
            "continuation_after": False,
            "method": "full-page-fallback",
        }

    hit_blocks = {int(ordered[index].get("block", 0)) for index in hits}
    selected_blocks: set[int] = set(hit_blocks)
    for block in list(hit_blocks):
        selected_blocks.update({max(0, block - 1), block + 1})

    blocks: dict[int, list[dict[str, Any]]] = {}
    for line in ordered:
        blocks.setdefault(int(line.get("block", 0)), []).append(line)

    # Add one likely heading immediately before a selected block.
    for block in sorted(list(hit_blocks)):
        previous = blocks.get(block - 1, [])
        if previous and _looks_like_heading(" ".join(item["text"] for item in previous)):
            selected_blocks.add(block - 1)

    selected_lines = [line for line in ordered if int(line.get("block", 0)) in selected_blocks]
    context = "\n".join(line["text"] for line in selected_lines).strip()
    if len(context) > maximum_characters:
        normalized_context = _normalise(context)
        first_term = next((_normalise(term) for term in terms if _compact(term)), "")
        position = normalized_context.find(first_term) if first_term else -1
        if position < 0:
            context = context[:maximum_characters]
        else:
            start = max(0, position - maximum_characters // 3)
            context = context[start : start + maximum_characters]

    known_y = [line["y"] for line in ordered if line.get("y") is not None]
    declared_heights = [
        line["page_height"] for line in ordered if line.get("page_height") is not None
    ]
    page_max = max(declared_heights) if declared_heights else (max(known_y) if known_y else 0)
    selected_y = [line["y"] for line in selected_lines if line.get("y") is not None]
    first_text = selected_lines[0]["text"] if selected_lines else ""
    last_text = selected_lines[-1]["text"] if selected_lines else ""
    continuation_before = bool(
        page_max
        and selected_y
        and min(selected_y) <= page_max * 0.12
        and (first_text[:1].islower() or first_text.startswith((",", ";", ")", "—")))
    )
    continuation_after = bool(
        page_max
        and selected_y
        and max(selected_y) >= page_max * 0.86
        and not re.search(r"[.!?…]['\"»)]?$", last_text.strip())
    ) or bool(re.search(r"\b(?:continua|continuara|seguira)\b", _normalise(last_text)))

    return {
        "context": context,
        "continuation_before": continuation_before,
        "continuation_after": continuation_after,
        "method": "alto-blocks-and-geometry",
    }


def _tail_for_continuation(text: str, maximum: int = 1800) -> str:
    compact = text.strip()
    return compact[-maximum:] if len(compact) > maximum else compact


def _head_for_continuation(text: str, maximum: int = 1800) -> str:
    compact = text.strip()
    return compact[:maximum]


def _sentences(text: str) -> list[str]:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if not compact:
        return []
    return [item.strip() for item in re.split(r"(?<=[.;!?…])\s+", compact) if item.strip()]


def extract_family_relations(text: str, subject_name: str) -> list[dict[str, Any]]:
    """Extract conservative, subject-anchored kinship formulations.

    A relationship is ignored when the sentence and its immediately preceding
    sentence do not identify the target. This prevents an unrelated obituary
    on the same newspaper page from being attached to the person under study.
    """
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    subject_normalized = _normalise(subject_name)
    subject_tokens = [
        token for token in re.findall(r"[a-z0-9]+", subject_normalized) if len(token) >= 3
    ]

    def subject_present(sentence: str) -> bool:
        normalized = _normalise(sentence)
        if subject_normalized and subject_normalized in normalized:
            return True
        if not subject_tokens:
            return False
        # Requiring all substantial components is deliberately conservative.
        return all(token in normalized for token in subject_tokens)

    def add(relation_type: str, relative_name: str, evidence: str, confidence: float) -> None:
        cleaned = _safe_name(relative_name)
        if not cleaned or _normalise(cleaned) == subject_normalized:
            return
        key = (relation_type, _normalise(cleaned))
        if key in seen:
            return
        seen.add(key)
        output.append(
            {
                "relation_type": relation_type,
                "relative_name": cleaned,
                "evidence": _compact(evidence)[:1200],
                "confidence": confidence,
            }
        )

    name = r"(?:don|doña|d\.?|dª)?\s*([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ'’-]+(?:\s+[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ'’-]+){1,5})"
    sentences = _sentences(text)
    previous_anchored = False
    for sentence in sentences:
        anchored = subject_present(sentence) or previous_anchored
        current_has_subject = subject_present(sentence)
        previous_anchored = current_has_subject
        if not anchored:
            continue

        for relation_type, pattern in (
            ("cónyuge", rf"\b(?:su\s+)?(?:espos[ao]|marido|mujer)\s*[,;:]?\s*{name}"),
            ("padre", rf"\bpadre\s*[,;:]?\s*{name}"),
            ("madre", rf"\bmadre\s*[,;:]?\s*{name}"),
            ("hermano", rf"\bherman[oa]\s*[,;:]?\s*{name}"),
            ("hijo", rf"\bhij[oa]\s*[,;:]?\s*{name}"),
            ("yerno", rf"\byerno\s*[,;:]?\s*{name}"),
            ("nuera", rf"\bnuera\s*[,;:]?\s*{name}"),
            ("sobrino", rf"\bsobrin[oa]\s*[,;:]?\s*{name}"),
        ):
            for match in re.finditer(pattern, sentence):
                add(relation_type, match.group(1), sentence, 0.88)

        child_of = re.search(
            rf"\bhij[oa]\s+de\s+{name}(?:\s+y\s+(?:de\s+)?{name})?",
            sentence,
        )
        if child_of:
            add("progenitor", child_of.group(1), sentence, 0.94)
            if child_of.lastindex and child_of.lastindex >= 2 and child_of.group(2):
                add("progenitor", child_of.group(2), sentence, 0.94)

        children = re.search(r"\bhijos?\s*[,;:]\s*(.+)", sentence, flags=re.I)
        if children:
            tail = children.group(1)
            for match in re.finditer(name, tail):
                add("hijo", match.group(1), sentence, 0.84)

    return output


_default_engine: GalicianaInvestigationEngine | None = None
_default_lock = threading.Lock()


def get_default_engine() -> GalicianaInvestigationEngine:
    global _default_engine
    if _default_engine is None:
        with _default_lock:
            if _default_engine is None:
                _default_engine = GalicianaInvestigationEngine()
    return _default_engine
