from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import httpx

from .base import BaseConnector
from ..models import GenealogyQuery, SearchResult
from ..query_expansion import expand_query


SPARQL_ENDPOINT = "https://datos-abertos.galiciana.gal/arpad-bibdix-lod/sparql"
OBJECTS_GRAPH = "urn:galiciana:objetos-digitales"


def _sparql_literal(value: str) -> str:
    value = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", " ")
        .replace("\n", " ")
    )
    return f'"{value}"'


def _compact_terms(values: Iterable[str], maximum: int = 8) -> list[str]:
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


def build_person_sparql(query: GenealogyQuery, limit: int = 20) -> str:
    limit = max(1, min(limit, 100))
    terms = _compact_terms(expand_query(query))
    if not terms:
        terms = _compact_terms([query.name])

    values = " ".join(_sparql_literal(term) for term in terms)
    date_filters: list[str] = []

    if query.year_from is not None:
        date_filters.append(
            f'(!BOUND(?fecha) || SUBSTR(STR(?fecha), 1, 4) >= "{query.year_from:04d}")'
        )
    if query.year_to is not None:
        date_filters.append(
            f'(!BOUND(?fecha) || SUBSTR(STR(?fecha), 1, 4) <= "{query.year_to:04d}")'
        )

    date_clause = ""
    if date_filters:
        date_clause = "\n  FILTER(" + " && ".join(date_filters) + ")"

    return f"""
PREFIX dc: <http://purl.org/dc/elements/1.1/>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX edm: <http://www.europeana.eu/schemas/edm/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

SELECT DISTINCT ?obra ?titulo ?autor ?fecha ?descripcion ?lugar ?shownAt
WHERE {{
  GRAPH <{OBJECTS_GRAPH}> {{
    ?obra dc:title ?titulo .

    OPTIONAL {{
      ?obra dc:creator ?creator .
      OPTIONAL {{ ?creator skos:prefLabel ?autor }}
    }}
    OPTIONAL {{ ?obra dc:date ?fecha }}
    OPTIONAL {{ ?obra dc:description ?descripcion }}
    OPTIONAL {{ ?obra dcterms:spatial ?lugar }}
    OPTIONAL {{
      ?aggregation edm:aggregatedCHO ?obra .
      OPTIONAL {{ ?aggregation edm:isShownAt ?shownAt }}
    }}

    BIND(LCASE(STR(?titulo)) AS ?tituloTexto)
    BIND(LCASE(COALESCE(STR(?autor), "")) AS ?autorTexto)
    BIND(LCASE(COALESCE(STR(?descripcion), "")) AS ?descripcionTexto)
    BIND(LCASE(COALESCE(STR(?lugar), "")) AS ?lugarTexto)

    VALUES ?aguja {{ {values} }}

    FILTER(
      CONTAINS(?tituloTexto, ?aguja) ||
      CONTAINS(?autorTexto, ?aguja) ||
      CONTAINS(?descripcionTexto, ?aguja) ||
      CONTAINS(?lugarTexto, ?aguja)
    )
  }}{date_clause}
}}
LIMIT {limit}
""".strip()


def _value(binding: dict[str, Any], key: str) -> str | None:
    node = binding.get(key)
    if not isinstance(node, dict):
        return None
    value = node.get("value")
    return value if isinstance(value, str) and value.strip() else None


def parse_sparql_results(payload: dict[str, Any], query: GenealogyQuery) -> list[SearchResult]:
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
                raw=binding,
            )
        )

    return results


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
            "User-Agent": "RobGenealogia/0.3",
        }
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            transport=self.transport,
            headers=headers,
        ) as client:
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
        sparql = build_person_sparql(query, limit=limit)
        payload = await self._execute(sparql)
        return parse_sparql_results(payload, query)

    async def healthcheck(self) -> dict[str, str | bool]:
        sparql = f"""
ASK {{
  GRAPH <{OBJECTS_GRAPH}> {{
    ?s ?p ?o
  }}
}}
""".strip()
        payload = await self._execute(sparql)
        return {
            "source_id": self.source_id,
            "ok": bool(payload.get("boolean")),
            "endpoint": SPARQL_ENDPOINT,
            "capability": "búsqueda en metadatos mediante SPARQL",
        }
