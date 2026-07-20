from __future__ import annotations

import asyncio
import ipaddress
import os
import re
from time import monotonic
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from rob.connectors.exa import ExaAPIError, ExaClient

from .exa_store import ExaInvestigationStore
from .models import InvestigationTarget, SourceCapabilities


def _configured_int(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return min(max(value, 1), maximum)


def _canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    query = urlencode(
        sorted(
            (key, item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_")
        )
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), path, query, ""))


def _queries(target: InvestigationTarget, maximum: int) -> list[str]:
    names = [target.name, *target.variants]
    qualifiers = [*target.places]
    if target.year_from is not None or target.year_to is not None:
        qualifiers.append(
            f"{target.year_from or ''}-{target.year_to or ''}".strip("-")
        )
    qualifiers.extend(value for value in (target.spouse, target.profession) if value)
    output: list[str] = []
    seen: set[str] = set()
    candidates: list[str] = []
    candidates.extend(f'"{str(name).strip()}"' for name in names if str(name).strip())
    for qualifier in qualifiers:
        candidates.extend(
            f'"{str(name).strip()}" "{qualifier}"'
            for name in names
            if str(name).strip()
        )
    for query in candidates:
        key = re.sub(r"\s+", " ", query).casefold()
        if key not in seen:
            seen.add(key)
            output.append(query)
        if len(output) >= maximum:
            return output
    return output


def _status_for_error(exc: BaseException, attempts: int) -> str:
    status_code = getattr(exc, "status_code", None)
    retryable = status_code is None or status_code == 429 or status_code in {500, 502, 503, 504}
    return "retryable" if retryable and attempts < 3 else "failed"


def _safe_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("La URL Exa debe ser http/https, tener hostname y no incluir credenciales.")
    hostname = parts.hostname.casefold().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".local"):
        raise ValueError("La URL local no está permitida.")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private or address.is_loopback or address.is_link_local
        or address.is_multicast or address.is_reserved or address.is_unspecified
    ):
        raise ValueError("La dirección IP no es pública.")
    return value.strip()


class ExaSourceAdapter:
    source_name = "exa"
    capabilities = SourceCapabilities()

    def __init__(
        self,
        store: ExaInvestigationStore,
        *,
        api_key: str | None = None,
        client: ExaClient | None = None,
    ) -> None:
        self.store = store
        self.api_key = os.getenv("EXA_API_KEY", "").strip() if api_key is None else api_key.strip()
        self.client = client or ExaClient(self.api_key)
        self.maximum_queries = _configured_int("EXA_MAX_QUERIES_PER_INVESTIGATION", 6, 10)
        self.maximum_results = _configured_int("EXA_MAX_RESULTS_PER_INVESTIGATION", 40, 80)

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def create_investigation(
        self, target: InvestigationTarget, *, maximum_queries: int, maximum_results: int
    ) -> dict[str, Any]:
        if not self.available:
            raise ValueError("Fuente desconocida o no disponible: exa.")
        query_limit = min(max(maximum_queries, 1), self.maximum_queries, 10)
        result_limit = min(max(maximum_results, 1), self.maximum_results, 80)
        queries = _queries(target, query_limit)
        investigation_id = self.store.create(target.to_dict())
        diagnostics: list[dict[str, Any]] = []
        executed_queries: list[str] = []
        successful = 0
        unique = 0
        for query in queries:
            if unique >= result_limit:
                break
            executed_queries.append(query)
            try:
                response = await self.client.search(
                    query, num_results=min(result_limit - unique, 20)
                )
                successful += 1
                diagnostic = {"fuente": "exa", "query": query, "ok": True}
                diagnostic.update(response.get("diagnostics", {}))
                diagnostics.append(diagnostic)
                for item in response["results"]:
                    if unique >= result_limit:
                        break
                    if self.store.add_candidate(
                        investigation_id, unique, query, item, _canonical_url(item["url"])
                    ):
                        unique += 1
            except Exception as exc:
                diagnostics.append(
                    {"fuente": "exa", "query": query, "ok": False,
                     "error_type": type(exc).__name__, "error": str(exc)[:300]}
                )
        status = "processing" if unique else ("complete" if successful else "failed")
        self.store.finish_search(
            investigation_id,
            status=status,
            queries=executed_queries,
            diagnostics=diagnostics,
        )
        limits = {
            "resultados_solicitados": maximum_results,
            "resultados_efectivos": result_limit,
            "consultas_solicitadas": maximum_queries,
            "consultas_efectivas": query_limit,
            "total_candidatos_unicos": unique,
        }
        return {
            "source_investigation_id": investigation_id,
            "status": status,
            "diagnostics": diagnostics,
            **limits,
            "detail": limits,
        }

    def _summary(self, investigation_id: str) -> dict[str, Any]:
        counts = self.store.stats(investigation_id)
        total = sum(counts.values())
        open_count = counts["pending"] + counts["reading"] + counts["retryable"]
        status = "processing" if open_count else (
            "complete" if counts["read"] or total == 0 else "failed"
        )
        self.store.finish_search(
            investigation_id,
            status=status,
            queries=self.store.require(investigation_id)["queries"],
            diagnostics=self.store.require(investigation_id)["diagnostics"],
        )
        return {"status": status, "counts": counts, "total": total}

    async def process_next_batch(
        self, source_investigation_id: str, *, batch_size: int, time_budget_seconds: int
    ) -> dict[str, Any]:
        investigation = self.store.require(source_investigation_id)
        deadline = monotonic() + max(0.0, float(time_budget_seconds))
        claimed = self.store.claim(source_investigation_id, max(1, batch_size))
        read = failed = 0
        if claimed:
            try:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("Presupuesto Exa agotado.")
                target = InvestigationTarget.from_dict(investigation["target"])
                rich_query = " ".join(
                    value for value in [target.name, *target.variants, *target.places,
                                        target.spouse or "", target.profession or ""] if value
                )
                async with asyncio.timeout(remaining):
                    response = await self.client.contents(
                        [item["url"] for item in claimed],
                        highlight_query=rich_query,
                        max_characters=8000,
                    )
                if response.get("diagnostics"):
                    self.store.append_diagnostic(
                        source_investigation_id,
                        {
                            "fuente": "exa",
                            "operacion": "contents",
                            "ok": True,
                            **response["diagnostics"],
                        },
                    )
                returned = {
                    _canonical_url(item["url"]): item
                    for item in response["results"] if item.get("url")
                }
                for item in claimed:
                    data = returned.get(item["canonical_url"])
                    if data is None:
                        status = "retryable" if item["attempts"] < 3 else "failed"
                        self.store.mark(item["id"], status=status, error="Exa omitió la URL reclamada.")
                        failed += status == "failed"
                    else:
                        remaining_text = 8000
                        bounded_highlights: list[str] = []
                        for highlight in data.get("highlights", []):
                            if remaining_text <= 0:
                                break
                            bounded_highlights.append(str(highlight)[:remaining_text])
                            remaining_text -= len(bounded_highlights[-1])
                        data["highlights"] = bounded_highlights
                        data["text"] = str(data.get("text") or "")[:remaining_text]
                        self.store.mark(item["id"], status="read", data=data)
                        read += 1
            except BaseException as exc:
                for item in claimed:
                    status = _status_for_error(exc, item["attempts"])
                    self.store.mark(item["id"], status=status, error=str(exc)[:300])
                    failed += status == "failed"
                if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                    raise
        summary = self._summary(source_investigation_id)
        counts = summary["counts"]
        counters = {
                "total_resultados": summary["total"], "resultados_leidos": counts["read"],
                "resultados_pendientes": counts["pending"],
                "resultados_reintentables": counts["retryable"],
                "resultados_fallidos": counts["failed"],
                "procesados_en_esta_llamada": len(claimed), "leidos_en_esta_llamada": read,
                "fallidos_en_esta_llamada": failed,
                "cobertura": counts["read"] / summary["total"] if summary["total"] else 1.0,
                "completada": summary["status"] != "processing",
                "siguiente_paso": "Llama otra vez a procesarInvestigacion." if summary["status"] == "processing" else "Llama a obtenerInformeInvestigacion.",
        }
        return {
            "status": summary["status"], "complete": summary["status"] != "processing",
            **counters,
            "detail": counters,
        }

    def get_report(
        self, source_investigation_id: str, *, offset: int, maximum_results: int,
        include_pending: bool,
    ) -> dict[str, Any]:
        investigation = self.store.require(source_investigation_id)
        counts = self.store.stats(source_investigation_id)
        total = sum(counts.values())
        rows = self.store.rows(source_investigation_id, offset, maximum_results)
        evidence: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        for row in rows:
            if row["status"] == "read":
                context = "\n".join(row["highlights"]) or str(row.get("recovered_text") or "")[:8000]
                evidence.append({
                    "fecha": row.get("published_date"), "publicacion": urlsplit(row["url"]).hostname,
                    "titulo": row.get("title"), "pagina": None, "autor": row.get("author"),
                    "url_pagina": row["url"], "url_imagen": row.get("image_url"), "url_ocr": None,
                    "contexto": context[:8000], "puntuacion": row.get("score"),
                    "consulta_origen": row["query_text"], "categorias": [],
                    "metadatos_exa": {"estado": "read", "intentos": row["attempts"], "fecha_lectura": row.get("read_at")},
                })
            elif include_pending:
                pending.append({"titulo": row.get("title"), "url_pagina": row["url"],
                                "consulta_origen": row["query_text"], "estado_lectura": row["status"],
                                "error": row.get("error")})
        return {
            "cobertura": counts["read"] / total if total else 1.0,
            "total_resultados": total, "paginas_leidas": counts["read"],
            "paginas_pendientes": counts["pending"] + counts["reading"] + counts["retryable"],
            "paginas_fallidas": counts["failed"], "evidencias_documentales": evidence,
            "paginas_no_leidas": pending, "familia_documentada_o_candidata": [],
            "diagnosticos_fuente": investigation["diagnostics"],
            "paginacion": {"desde": offset, "devueltos": len(rows),
                           "siguiente_desde": offset + len(rows) if offset + len(rows) < total else None},
            "source_investigation_id": source_investigation_id,
        }

    async def read_source(
        self, source_url: str, *, terms: list[str], maximum_characters: int
    ) -> dict[str, Any]:
        source_url = _safe_url(source_url)
        maximum = max(1, min(maximum_characters, 30000))
        response = await self.client.contents(
            [source_url], highlight_query=" ".join(terms) or source_url, max_characters=min(maximum, 8000)
        )
        result = next((item for item in response["results"] if item.get("url")), None)
        if result is None:
            raise ExaAPIError("Exa no devolvió el documento solicitado.")
        remaining = maximum
        contexts: list[str] = []
        for highlight in result["highlights"]:
            if remaining <= 0:
                break
            contexts.append(highlight[:remaining])
            remaining -= len(contexts[-1])
        content = "" if contexts else result["text"][:remaining]
        return {
            "fuente": "exa", "estado": "ok", "source_url": result["url"],
            "content": content, "contextos": contexts,
            "documento": {"titulo": result.get("title"), "autor": result.get("author"),
                          "fecha": result.get("publishedDate"), "url_imagen": result.get("image"),
                          "url_pagina": result["url"]},
            "detalle_fuente": {**response.get("diagnostics", {}),
                               "longitud_texto_original": len(result.get("text") or "")},
        }
