from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import httpx

from .base import BaseConnector
from ..models import GenealogyQuery, SearchResult
from ..query_expansion import expand_query


SPARQL_ENDPOINT = "https://datos-abertos.galiciana.gal/arpad-bibdix-lod/sparql"
OBJECTS_GRAPH = "urn:galiciana:objetos-digitales"
MAX_TERMS = 4


def _sparql_literal(value: str) -> str:
    value = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", " ")
        .replace("\n", " ")
    )
    return f'"{value}"'


def _compact_terms(values: Iterable[str], maximum: int = MAX_TERMS) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    for value in values:
        cleaned = " ".join(value.replace('"', " ").split()).casefold()
        if len(cleaned) < 3 or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
        if len(result) >= maximum:
            break

    return result


def build_term_sparql(
    query: GenealogyQuery,
    term: str,
    limit: int = 20,
) -> str:
    """
    Construye una consulta SPARQL compacta para un único término.

    Galiciana admite GET en su formulario público. Una consulta enorme con
    todas las variantes en una sola URL puede provocar HTTP 414; por eso
    cada variante se consulta por separado y luego se fusionan resultados.
    """
    limit = max(1, min(limit, 100))
    term_literal = _sparql_literal(term.casefold())
    date_filters: list[str] = []

    if query.year_from is not None:
        date_filters.append(
            f'(!BOUND(?fecha)||SUBSTR(STR(?fecha),1,4)>="{query.year_from:04d}")'
        )
    if query.year_to is not None:
        date_filters.append(
            f'(!BOUND(?fecha)||SUBSTR(STR(?fecha),1,4)<="{query.year_to:04d}")'
        )

    date_clause = ""
    if date_filters:
        date_clause = " FILTER(" + "&&".join(date_filters) + ")"

    # Se mantiene deliberadamente compacto: el transporte es GET.
    return (
        "PREFIX dc:<http://purl.org/dc/elements/1.1/> "
        "PREFIX dct:<http://purl.org/dc/terms/> "
        "PREFIX edm:<http://www.europeana.eu/schemas/edm/> "
        "PREFIX skos:<http://www.w3.org/2004/02/skos/core#> "
        "SELECT DISTINCT ?obra ?titulo ?autor ?fecha ?descripcion ?lugar ?shownAt WHERE{"
        f"GRAPH <{OBJECTS_GRAPH}>{{"
        "?obra dc:title ?titulo."
        "OPTIONAL{?obra dc:creator ?creator.OPTIONAL{?creator skos:prefLabel ?autor}}"
        "OPTIONAL{?obra dc:date ?fecha}"
        "OPTIONAL{?obra dc:description ?descripcion}"
        "OPTIONAL{?obra dct:spatial ?lugarNodo."
        "OPTIONAL{?lugarNodo skos:prefLabel ?lugar}}"
        "OPTIONAL{?aggregation edm:aggregatedCHO ?obra."
        "OPTIONAL{?aggregation edm:isShownAt ?shownAt}}"
        "BIND(LCASE(CONCAT(STR(?titulo),' ',COALESCE(STR(?autor),''),' ',"
        "COALESCE(STR(?descripcion),''),' ',COALESCE(STR(?lugar),''))) AS ?texto)"
        f"FILTER(CONTAINS(?texto,{term_literal}))"
        f"}}{date_clause}}}"
        f" LIMIT {limit}"
    )


def build_person_sparql(query: GenealogyQuery, limit: int = 20) -> str:
    """
    Compatibilidad: devuelve la consulta compacta del primer término.

    La búsqueda real usa varias consultas cortas mediante `search`.
    """
    terms = _compact_terms(expand_query(query))
    if not terms:
        terms = _compact_terms([query.name])
    return build_term_sparql(query, terms[0], limit=limit)


def _value(binding: dict[str, Any], key: str) -> str | None:
    node = binding.get(key)
    if not isinstance(node, dict):
        return None
    value = node.get("value")
    return value if isinstance(value, str) and value.strip() else None


def parse_sparql_results(
    payload: dict[str, Any],
    query: GenealogyQuery,
    *,
    matched_term: str | None = None,
) -> list[SearchResult]:
    bindings = payload.get("results", {}).get("bindings", [])
    if not isinstance(bindings, list):
        return []

    name = query.name.casefold()
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

        searchable = " ".join(
            value for value in (title, author, description, place) if value
        ).casefold()

        reasons: list[str] = []
        score = 0.35
        if name and name in searchable:
            score += 0.45
            reasons.append("nombre completo en los metadatos")
        if any(place_name.casefold() in searchable for place_name in query.places):
            score += 0.15
            reasons.append("lugar compatible")
        if author and name in author.casefold():
            score += 0.05
            reasons.append("coincidencia en autoría")
        if matched_term and matched_term.casefold() in searchable:
            reasons.append(f'coincidencia con "{matched_term}"')

        raw = dict(binding)
        if matched_term:
            raw["_matched_term"] = matched_term

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
                score_reasons=reasons or ["coincidencia parcial en metadatos"],
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
        timeout: float = 35.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.timeout = timeout
        self.transport = transport

    async def _execute(self, sparql: str) -> dict[str, Any]:
        headers = {
            "Accept": "application/sparql-results+json, application/json",
            "User-Agent": "RobGenealogia/0.3.2",
        }
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            transport=self.transport,
            headers=headers,
        ) as client:
            # Galiciana rechaza el POST sin el flujo de su formulario (HTTP 403).
            # Se usa GET, pero con consultas deliberadamente cortas.
            response = await client.get(
                SPARQL_ENDPOINT,
                params={
                    "query": sparql,
                    "format": "application/sparql-results+json",
                },
            )
            response.raise_for_status()
            try:
                return response.json()
            except ValueError as exc:
                raise RuntimeError(
                    "Galiciana respondió, pero no devolvió JSON SPARQL."
                ) from exc

    async def search(
        self,
        query: GenealogyQuery,
        limit: int = 20,
    ) -> list[SearchResult]:
        limit = max(1, min(limit, 100))
        terms = _compact_terms(expand_query(query))
        if not terms:
            terms = _compact_terms([query.name])

        merged: dict[tuple[str, str, str], SearchResult] = {}

        # Consultas secuenciales para no castigar el servicio público.
        for term in terms:
            sparql = build_term_sparql(query, term, limit=limit)
            payload = await self._execute(sparql)
            for result in parse_sparql_results(
                payload,
                query,
                matched_term=term,
            ):
                key = _result_key(result)
                previous = merged.get(key)
                if previous is None or result.score > previous.score:
                    merged[key] = result
                elif previous is not None:
                    for reason in result.score_reasons:
                        if reason not in previous.score_reasons:
                            previous.score_reasons.append(reason)

        return sorted(
            merged.values(),
            key=lambda item: (-item.score, item.date or "", item.title),
        )[:limit]

    async def healthcheck(self) -> dict[str, str | bool]:
        sparql = (
            f"ASK{{GRAPH <{OBJECTS_GRAPH}>{{?s ?p ?o}}}}"
        )
        payload = await self._execute(sparql)
        return {
            "source_id": self.source_id,
            "ok": bool(payload.get("boolean")),
            "endpoint": SPARQL_ENDPOINT,
            "capability": "búsqueda en metadatos mediante SPARQL",
        }
