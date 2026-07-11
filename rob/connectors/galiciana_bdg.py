from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from .base import BaseConnector
from ..models import GenealogyQuery, SearchResult
from ..query_expansion import remove_accents


SPARQL_ENDPOINT = "https://datos-abertos.galiciana.gal/arpad-bibdix-lod/sparql"
OBJECTS_GRAPH = "urn:galiciana:objetos-digitales"

SearchField = Literal["author", "title", "description"]


@dataclass(slots=True)
class SearchDiagnostic:
    term: str
    field: SearchField
    ok: bool
    elapsed_ms: int
    result_count: int = 0
    error_type: str | None = None
    error: str | None = None


@dataclass(slots=True)
class GalicianaSearchReport:
    results: list[SearchResult] = field(default_factory=list)
    diagnostics: list[SearchDiagnostic] = field(default_factory=list)

    @property
    def successful_requests(self) -> int:
        return sum(1 for item in self.diagnostics if item.ok)

    @property
    def failed_requests(self) -> int:
        return sum(1 for item in self.diagnostics if not item.ok)

    @property
    def status(self) -> str:
        if self.successful_requests == 0:
            return "unavailable"
        if self.failed_requests:
            return "partial"
        return "ok"


def _sparql_literal(value: str) -> str:
    value = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", " ")
        .replace("\n", " ")
    )
    return f'"{value}"'


def _metadata_terms(query: GenealogyQuery, maximum: int = 3) -> list[str]:
    """
    Variantes útiles para metadatos.

    No mezcla el nombre con cónyuge o lugares: esas combinaciones alargaban la
    consulta y rara vez existen literalmente en un campo bibliográfico.
    """
    candidates = [query.name, *query.variants]
    expanded: list[str] = []
    seen: set[str] = set()

    for candidate in candidates:
        clean = " ".join(candidate.replace('"', " ").split()).casefold()
        for value in (clean, remove_accents(clean)):
            if len(value) < 3 or value in seen:
                continue
            seen.add(value)
            expanded.append(value)
            if len(expanded) >= maximum:
                return expanded

    return expanded


def _field_pattern(field_name: SearchField, term_literal: str) -> str:
    if field_name == "author":
        return (
            "?obra dc:creator ?creator."
            "?creator skos:prefLabel ?autor."
            f"FILTER(CONTAINS(LCASE(STR(?autor)),{term_literal}))"
        )
    if field_name == "title":
        return (
            "?obra dc:title ?titulo."
            f"FILTER(CONTAINS(LCASE(STR(?titulo)),{term_literal}))"
        )
    if field_name == "description":
        return (
            "?obra dc:description ?descripcion."
            f"FILTER(CONTAINS(LCASE(STR(?descripcion)),{term_literal}))"
        )
    raise ValueError(f"Campo no soportado: {field_name}")


def build_field_sparql(
    term: str,
    field_name: SearchField,
    limit: int = 20,
) -> str:
    """
    Consulta un campo ya indexable/bindable antes de filtrar.

    Evita el barrido anterior de todo el grafo con CONCAT sobre varios
    OPTIONAL, que podía agotar el tiempo de respuesta del endpoint.
    """
    limit = max(1, min(limit, 50))
    term_literal = _sparql_literal(term.casefold())
    match_pattern = _field_pattern(field_name, term_literal)

    return (
        "PREFIX dc:<http://purl.org/dc/elements/1.1/> "
        "PREFIX dcterms:<http://purl.org/dc/terms/> "
        "PREFIX edm:<http://www.europeana.eu/schemas/edm/> "
        "PREFIX skos:<http://www.w3.org/2004/02/skos/core#> "
        "SELECT DISTINCT ?obra ?titulo ?autor ?fecha ?descripcion ?lugar ?shownAt WHERE{"
        f"{match_pattern}"
        "OPTIONAL{?obra dc:title ?titulo}"
        "OPTIONAL{?obra dc:creator ?creator2."
        "OPTIONAL{?creator2 skos:prefLabel ?autor}}"
        "OPTIONAL{?obra dc:date ?fecha}"
        "OPTIONAL{?obra dc:description ?descripcion}"
        "OPTIONAL{?obra dcterms:spatial ?lugarNodo."
        "OPTIONAL{?lugarNodo skos:prefLabel ?lugar}}"
        "OPTIONAL{?aggregation edm:aggregatedCHO ?obra."
        "OPTIONAL{?aggregation edm:isShownAt ?shownAt}}"
        f"}} LIMIT {limit}"
    )


def _value(binding: dict[str, Any], key: str) -> str | None:
    node = binding.get(key)
    if not isinstance(node, dict):
        return None
    value = node.get("value")
    return value if isinstance(value, str) and value.strip() else None


def _extract_year(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"(?<!\d)(1[0-9]{3}|20[0-9]{2})(?!\d)", value)
    return int(match.group(1)) if match else None


def _date_is_compatible(value: str | None, query: GenealogyQuery) -> bool:
    year = _extract_year(value)
    if year is None:
        return True
    if query.year_from is not None and year < query.year_from:
        return False
    if query.year_to is not None and year > query.year_to:
        return False
    return True


def parse_sparql_results(
    payload: dict[str, Any],
    query: GenealogyQuery,
    *,
    matched_term: str,
    matched_field: SearchField,
) -> list[SearchResult]:
    bindings = payload.get("results", {}).get("bindings", [])
    if not isinstance(bindings, list):
        return []

    canonical_name = query.name.casefold()
    results: list[SearchResult] = []

    for binding in bindings:
        if not isinstance(binding, dict):
            continue

        work_uri = _value(binding, "obra")
        title = _value(binding, "titulo") or "Registro sin título"
        author = _value(binding, "autor")
        description = _value(binding, "descripcion")
        place = _value(binding, "lugar")
        shown_at = _value(binding, "shownAt")
        date = _value(binding, "fecha")

        if not _date_is_compatible(date, query):
            continue

        searchable = " ".join(
            value for value in (title, author, description, place) if value
        ).casefold()

        score = 0.40
        reasons = [f'coincidencia en {matched_field} con "{matched_term}"']

        if canonical_name and canonical_name in searchable:
            score += 0.35
            reasons.append("nombre completo en los metadatos")

        if author and matched_term.casefold() in author.casefold():
            score += 0.10
            reasons.append("coincidencia en autoría")

        if any(place_name.casefold() in searchable for place_name in query.places):
            score += 0.10
            reasons.append("lugar compatible")

        year = _extract_year(date)
        if year is not None:
            score += 0.05
            reasons.append("fecha interpretable y compatible")

        raw = dict(binding)
        raw["_matched_term"] = matched_term
        raw["_matched_field"] = matched_field

        results.append(
            SearchResult(
                source_id="galiciana_bdg",
                source_name="Galiciana. Biblioteca Dixital de Galicia",
                territory="Galicia",
                title=title,
                url=shown_at or work_uri or SPARQL_ENDPOINT,
                matched_text=description or author,
                date=date,
                place=place,
                document_type="objeto bibliográfico digital",
                score=min(score, 1.0),
                score_reasons=reasons,
                raw=raw,
            )
        )

    return results


def _result_key(result: SearchResult) -> tuple[str, str, str]:
    work_uri = _value(result.raw, "obra") or ""
    return (
        work_uri.casefold(),
        result.title.casefold(),
        (result.date or "").casefold(),
    )


class GalicianaBDGConnector(BaseConnector):
    source_id = "galiciana_bdg"

    def __init__(
        self,
        *,
        timeout: float = 22.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.timeout = timeout
        self.transport = transport

    async def _execute(self, sparql: str) -> dict[str, Any]:
        timeout = httpx.Timeout(
            connect=min(self.timeout, 10.0),
            read=self.timeout,
            write=min(self.timeout, 10.0),
            pool=min(self.timeout, 10.0),
        )
        headers = {
            "Accept": "application/sparql-results+json, application/json",
            "User-Agent": "RobGenealogia/0.3.3",
        }

        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            transport=self.transport,
            headers=headers,
        ) as client:
            response = await client.get(
                SPARQL_ENDPOINT,
                params={
                    "query": sparql,
                    "format": "application/sparql-results+json",
                    "default-graph-uri": OBJECTS_GRAPH,
                },
            )
            response.raise_for_status()
            try:
                return response.json()
            except ValueError as exc:
                content_type = response.headers.get("content-type", "desconocido")
                raise RuntimeError(
                    "Galiciana respondió sin JSON SPARQL "
                    f"(content-type: {content_type})."
                ) from exc

    async def search_with_diagnostics(
        self,
        query: GenealogyQuery,
        limit: int = 20,
    ) -> GalicianaSearchReport:
        limit = max(1, min(limit, 50))
        terms = _metadata_terms(query)
        report = GalicianaSearchReport()
        merged: dict[tuple[str, str, str], SearchResult] = {}

        # Orden de coste/valor: autor y título primero; descripción después.
        fields: tuple[SearchField, ...] = ("author", "title", "description")

        for term in terms:
            for field_name in fields:
                started = time.perf_counter()
                try:
                    sparql = build_field_sparql(
                        term,
                        field_name,
                        limit=max(limit, 10),
                    )
                    payload = await self._execute(sparql)
                    parsed = parse_sparql_results(
                        payload,
                        query,
                        matched_term=term,
                        matched_field=field_name,
                    )
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    report.diagnostics.append(
                        SearchDiagnostic(
                            term=term,
                            field=field_name,
                            ok=True,
                            elapsed_ms=elapsed_ms,
                            result_count=len(parsed),
                        )
                    )

                    for result in parsed:
                        key = _result_key(result)
                        previous = merged.get(key)
                        if previous is None or result.score > previous.score:
                            merged[key] = result
                        elif previous is not None:
                            for reason in result.score_reasons:
                                if reason not in previous.score_reasons:
                                    previous.score_reasons.append(reason)

                    if len(merged) >= limit:
                        break

                except Exception as exc:
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    message = str(exc).strip() or repr(exc)
                    report.diagnostics.append(
                        SearchDiagnostic(
                            term=term,
                            field=field_name,
                            ok=False,
                            elapsed_ms=elapsed_ms,
                            error_type=type(exc).__name__,
                            error=message,
                        )
                    )
                    # Una búsqueda parcial es preferible a abortar todo el MCP.
                    continue

            if len(merged) >= limit:
                break

        report.results = sorted(
            merged.values(),
            key=lambda item: (-item.score, item.date or "", item.title),
        )[:limit]
        return report

    async def search(
        self,
        query: GenealogyQuery,
        limit: int = 20,
    ) -> list[SearchResult]:
        report = await self.search_with_diagnostics(query, limit=limit)
        return report.results

    async def healthcheck(self) -> dict[str, str | bool]:
        payload = await self._execute("ASK{?s ?p ?o}")
        return {
            "source_id": self.source_id,
            "ok": bool(payload.get("boolean")),
            "endpoint": SPARQL_ENDPOINT,
            "capability": "búsqueda selectiva en metadatos mediante SPARQL",
        }
