from __future__ import annotations

import asyncio
import os
from dataclasses import asdict
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from rob.connectors.galiciana_bdg import GalicianaBDGConnector
from rob.connectors.europeana_galicia import EuropeanaGaliciaConnector
from rob.connectors.oai_pmh import (
    GALICIANA_ADG_OAI,
    GALICIANA_BDG_OAI,
    OAIClient,
)
from rob.models import GenealogyQuery
from rob.query_expansion import expand_query
from rob.registry import list_registered_sources
from rob.sources import source_summary


EUROPEANA_SEARCH_URL = "https://api.europeana.eu/record/v2/search.json"
EUROPEANA_RECORD_URL = "https://api.europeana.eu/record/v2/{record_id}.json"

mcp = FastMCP(
    "Rob — Metabuscador Genealógico",
    host="0.0.0.0",
    port=int(os.getenv("PORT", "8000")),
    stateless_http=True,
    json_response=True,
)


def _api_key() -> str:
    key = os.getenv("EUROPEANA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Falta EUROPEANA_API_KEY.")
    return key


def _clean_europeana_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "title": item.get("title", []),
        "creator": item.get("dcCreator", []),
        "date": item.get("year", []) or item.get("dcDate", []),
        "description": item.get("dcDescription", []),
        "place": item.get("edmPlaceLabel", []),
        "provider": item.get("provider", []),
        "data_provider": item.get("dataProvider", []),
        "country": item.get("country", []),
        "type": item.get("type"),
        "rights": item.get("rights", []),
        "preview": item.get("edmPreview", []),
        "source_url": item.get("guid"),
    }


@mcp.tool()
def estado() -> dict[str, Any]:
    """Muestra la versión y el estado declarado de las fuentes."""
    return {
        "servidor": "Rob — Metabuscador Genealógico",
        "version": "0.4.0",
        "resumen_fuentes": source_summary(),
        "europeana_configurada": bool(os.getenv("EUROPEANA_API_KEY", "").strip()),
        "nota": "development no significa verificado; la prueba real se hace contra cada portal.",
    }


@mcp.tool()
def listar_fuentes(territorio: str = "") -> list[dict[str, Any]]:
    """Lista las fuentes registradas y su estado real de implementación."""
    sources = list_registered_sources()
    if not territorio.strip():
        return sources
    wanted = territorio.casefold().strip()
    return [
        source
        for source in sources
        if str(source["territory"]).casefold() == wanted
    ]


@mcp.tool()
def expandir_busqueda_persona(
    nombre: str,
    variantes: list[str] | None = None,
    lugares: list[str] | None = None,
    conyuge: str = "",
    profesion: str = "",
) -> list[str]:
    """Genera variantes de consulta antes de buscar en los portales."""
    query = GenealogyQuery(
        name=nombre,
        variants=variantes or [],
        places=lugares or [],
        spouse=conyuge or None,
        profession=profesion or None,
    )
    return expand_query(query)


@mcp.tool()
async def buscar_galiciana_metadatos(
    nombre: str,
    variantes: list[str] | None = None,
    lugares: list[str] | None = None,
    fecha_desde: int | None = None,
    fecha_hasta: int | None = None,
    conyuge: str = "",
    profesion: str = "",
    filas: int = 20,
) -> dict[str, Any]:
    """
    Busca en los metadatos abiertos de Galiciana-Biblioteca.

    Todavía no busca dentro del OCR de las páginas de prensa.
    """
    query = GenealogyQuery(
        name=nombre,
        variants=variantes or [],
        places=lugares or [],
        year_from=fecha_desde,
        year_to=fecha_hasta,
        spouse=conyuge or None,
        profession=profesion or None,
    )
    connector = GalicianaBDGConnector()
    report = await connector.search_with_diagnostics(query, limit=filas)
    return {
        "fuente": "Galiciana. Biblioteca Dixital de Galicia",
        "capacidad": "metadatos SPARQL; no OCR interno",
        "estado": report.status,
        "peticiones_correctas": report.successful_requests,
        "peticiones_fallidas": report.failed_requests,
        "total": len(report.results),
        "diagnostico": [asdict(item) for item in report.diagnostics],
        "resultados": [asdict(result) for result in report.results],
    }


@mcp.tool()
async def buscar_galicia_europeana(
    nombre: str,
    variantes: list[str] | None = None,
    lugares: list[str] | None = None,
    fecha_desde: int | None = None,
    fecha_hasta: int | None = None,
    conyuge: str = "",
    profesion: str = "",
    filas: int = 20,
) -> dict[str, Any]:
    """
    Busca personas en los metadatos de Galiciana cosechados por Europeana.

    Fuente principal estable de Galicia durante la primera fase. No busca
    todavía dentro del OCR de las páginas digitalizadas.
    """
    query = GenealogyQuery(
        name=nombre,
        variants=variantes or [],
        places=lugares or [],
        year_from=fecha_desde,
        year_to=fecha_hasta,
        spouse=conyuge or None,
        profession=profesion or None,
    )
    connector = EuropeanaGaliciaConnector(_api_key())
    report = await connector.search(query, limit=filas)
    return {
        "fuente": "Galiciana vía Europeana",
        "capacidad": "metadatos de Galiciana; no OCR interno",
        "estado": report.status,
        "consulta_europeana": report.query,
        "filtros": report.filters,
        "total_api": report.total_api,
        "total_devuelto": len(report.results),
        "error_type": report.error_type,
        "error": report.error,
        "resultados": [asdict(result) for result in report.results],
    }


@mcp.tool()
async def comprobar_galicia() -> dict[str, Any]:
    """Comprueba SPARQL y los dos repositorios OAI-PMH gallegos."""
    sparql = GalicianaBDGConnector()
    checks = await asyncio.gather(
        sparql.healthcheck(),
        OAIClient(GALICIANA_BDG_OAI).identify(),
        OAIClient(GALICIANA_ADG_OAI).identify(),
        return_exceptions=True,
    )

    names = ("galiciana_sparql", "galiciana_bdg_oai", "galiciana_adg_oai")
    output: dict[str, Any] = {}

    for name, result in zip(names, checks, strict=True):
        if isinstance(result, Exception):
            output[name] = {
                "ok": False,
                "error": f"{type(result).__name__}: {result}",
            }
        else:
            output[name] = result

    return output


@mcp.tool()
async def buscar_europeana(
    consulta: str,
    filas: int = 20,
    inicio: int = 1,
) -> dict[str, Any]:
    """Mantiene la búsqueda existente de Europeana."""
    filas = max(1, min(filas, 100))
    inicio = max(1, inicio)
    params = {
        "wskey": _api_key(),
        "query": consulta,
        "rows": filas,
        "start": inicio,
        "profile": "rich",
    }
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(EUROPEANA_SEARCH_URL, params=params)
        response.raise_for_status()
        data = response.json()
    return {
        "consulta": consulta,
        "total": data.get("totalResults", 0),
        "inicio": inicio,
        "resultados": [
            _clean_europeana_item(item)
            for item in data.get("items", [])
        ],
    }


@mcp.tool()
async def abrir_registro_europeana(record_id: str) -> dict[str, Any]:
    """Obtiene un registro completo de Europeana."""
    if not record_id.startswith("/"):
        record_id = "/" + record_id
    url = EUROPEANA_RECORD_URL.format(record_id=record_id)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(url, params={"wskey": _api_key()})
        response.raise_for_status()
        return response.json()


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
