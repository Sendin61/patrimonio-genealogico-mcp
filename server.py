from __future__ import annotations

import asyncio
import os
import re
import unicodedata
from dataclasses import asdict
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from rob.connectors.galiciana_bdg import GalicianaBDGConnector
from rob.connectors.galiciana_ocr import GalicianaOCRConnector
from rob.connectors.europeana_galicia import EuropeanaGaliciaConnector
from rob.connectors.oai_pmh import (
    GALICIANA_ADG_OAI,
    GALICIANA_BDG_OAI,
    OAIClient,
)
from rob.models import GenealogyQuery
from rob.galiciana_investigations import get_default_engine
from rob.query_expansion import expand_query
from rob.registry import list_registered_sources
from rob.sources import source_summary


EUROPEANA_SEARCH_URL = "https://api.europeana.eu/record/v2/search.json"
EUROPEANA_RECORD_URL = "https://api.europeana.eu/record/v2/{record_id}.json"
PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "https://patrimonio-genealogico-mcp.onrender.com",
).strip().rstrip("/")

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


def _normalise_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _extract_contexts(
    text: str,
    terms: list[str],
    *,
    radius: int = 900,
    maximum: int = 8,
) -> list[str]:
    """Extract compact, non-overlapping contexts around requested terms."""
    clean_terms = [term.strip() for term in terms if isinstance(term, str) and term.strip()]
    if not text.strip() or not clean_terms:
        return []

    normalised_text = _normalise_text(text)
    spans: list[tuple[int, int]] = []
    for term in clean_terms:
        target = _normalise_text(term)
        if not target:
            continue
        start = 0
        while len(spans) < maximum * 3:
            found = normalised_text.find(target, start)
            if found < 0:
                break
            spans.append((max(0, found - radius), min(len(text), found + len(term) + radius)))
            start = found + max(1, len(target))

    if not spans:
        return []

    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1] + 120:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    contexts: list[str] = []
    for start, end in merged[:maximum]:
        fragment = re.sub(r"\s+", " ", text[start:end]).strip()
        if fragment:
            contexts.append(fragment)
    return contexts


def _direct_image_url(image_id: str | None) -> str | None:
    if not image_id:
        return None
    return (
        "https://biblioteca.galiciana.gal/gl/catalogo_imagenes/"
        f"imagen_id.do?idImagen={image_id}"
    )


def _compact_galiciana_report(report: Any, *, maximum_results: int) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for item in report.mentions[:maximum_results]:
        results.append(
            {
                "fecha": item.date,
                "publicacion": item.parent_publication,
                "titulo": item.title,
                "pagina": item.page,
                "fragmentos": item.snippets[:4],
                "categorias": [value.category for value in item.interpretations],
                "puntuacion": item.score,
                "motivos_puntuacion": item.score_reasons,
                "url_pagina": item.page_url,
                "url_imagen": item.image_url or _direct_image_url(item.image_id),
            }
        )

    return {
        "fuente": "Galiciana — búsqueda OCR a texto completo",
        "estado": report.status,
        "consultas_realizadas": report.queries,
        "total_menciones_unicas": report.total_unique,
        "hallazgos": report.findings,
        "resultados": results,
        "nota": (
            "Los resultados se han localizado dentro del OCR de Galiciana. "
            "Para leer una página completa, utiliza la operación leerPaginaGaliciana "
            "con su url_pagina y los nombres que deban localizarse."
        ),
    }


def _query_from_payload(payload: dict[str, Any]) -> GenealogyQuery:
    nombre = str(payload.get("nombre") or "").strip()
    if not nombre:
        raise ValueError("Falta el campo obligatorio nombre.")
    variantes = [
        str(value).strip()
        for value in payload.get("variantes", [])
        if str(value).strip()
    ]
    lugares = [
        str(value).strip()
        for value in payload.get("lugares", [])
        if str(value).strip()
    ]
    return GenealogyQuery(
        name=nombre,
        variants=variantes,
        places=lugares,
        year_from=payload.get("fecha_desde"),
        year_to=payload.get("fecha_hasta"),
        spouse=str(payload.get("conyuge") or "").strip() or None,
        profession=str(payload.get("profesion") or "").strip() or None,
    )


@mcp.tool()
def estado() -> dict[str, Any]:
    """Muestra la versión y el estado declarado de las fuentes."""
    return {
        "servidor": "Rob — Metabuscador Genealógico",
        "version": "0.7.0",
        "resumen_fuentes": source_summary(),
        "europeana_configurada": bool(os.getenv("EUROPEANA_API_KEY", "").strip()),
        "actions_configuradas": True,
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
async def investigar_persona_galiciana(
    nombre: str,
    variantes: list[str] | None = None,
    lugares: list[str] | None = None,
    fecha_desde: int | None = None,
    fecha_hasta: int | None = None,
    conyuge: str = "",
    profesion: str = "",
    max_consultas: int = 6,
    max_resultados: int = 120,
    leer_paginas_completas: bool = False,
    max_paginas_completas: int = 40,
    concurrencia_lectura: int = 2,
) -> dict[str, Any]:
    """
    Investiga una persona dentro del OCR real de Galiciana.

    Genera variantes, recoge las páginas coincidentes, elimina duplicados,
    lee automáticamente el ALTO XML de las páginas seleccionadas, puntúa
    posibles homónimos y organiza una cronología y hallazgos temáticos.
    Los datos previos se usan únicamente para desambiguar: los hechos devueltos
    deben proceder de los fragmentos y documentos de Galiciana.
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
    connector = GalicianaOCRConnector()
    report = await connector.investigate(
        query,
        maximum_queries=max(1, min(max_consultas, 10)),
        maximum_results=max(1, min(max_resultados, 300)),
        read_full_pages=leer_paginas_completas,
        maximum_full_pages=max(0, min(max_paginas_completas, 100)),
        full_page_concurrency=max(1, min(concurrencia_lectura, 4)),
    )
    return {
        "fuente": "Galiciana — búsqueda OCR a texto completo",
        "capacidad": (
            "búsqueda dentro de páginas digitalizadas, lectura automática del OCR "
            "completo mediante METS/ALTO, evidencias ampliadas, cronología y "
            "desambiguación genealógica"
        ),
        "estado": report.status,
        "consultas_realizadas": report.queries,
        "diagnostico": [asdict(item) for item in report.diagnostics],
        "total_menciones_unicas": report.total_unique,
        "paginas_completas_solicitadas": report.full_pages_requested,
        "paginas_completas_leidas": report.full_pages_read,
        "paginas_completas_fallidas": report.full_pages_failed,
        "hallazgos": report.findings,
        "cronologia": report.chronology,
        "evidencias_documentales": [
            {
                "fecha": item.date,
                "titulo": item.title,
                "publicacion": item.parent_publication,
                "pagina": item.page,
                "contextos": item.expanded_contexts,
                "categorias": [value.category for value in item.interpretations],
                "puntuacion": item.score,
                "url_pagina": item.page_url,
                "url_ocr": item.ocr_url,
                "url_imagen": item.image_url,
            }
            for item in report.mentions
            if item.expanded_contexts
        ],
        "menciones": [asdict(item) for item in report.mentions],
        "nota_metodologica": report.note,
    }


@mcp.tool()
async def leer_pagina_galiciana(url_pagina: str) -> dict[str, Any]:
    """
    Abre una página o visor de Galiciana devuelto por la búsqueda OCR.

    Recupera el texto de página desde METS/ALTO cuando está disponible, además de imágenes y documentos.
    Solo acepta direcciones HTTPS de biblioteca.galiciana.gal.
    """
    connector = GalicianaOCRConnector()
    return await connector.read_page(url_pagina)


@mcp.tool()
async def crear_investigacion_galiciana(
    nombre: str,
    variantes: list[str] | None = None,
    lugares: list[str] | None = None,
    fecha_desde: int | None = None,
    fecha_hasta: int | None = None,
    conyuge: str = "",
    profesion: str = "",
    max_consultas: int = 8,
    max_resultados: int = 80,
) -> dict[str, Any]:
    """Crea un expediente reanudable: busca, deduplica y deja las páginas pendientes de lectura ALTO."""
    query = GenealogyQuery(
        name=nombre,
        variants=variantes or [],
        places=lugares or [],
        year_from=fecha_desde,
        year_to=fecha_hasta,
        spouse=conyuge or None,
        profession=profesion or None,
    )
    return await get_default_engine().create_investigation(
        query,
        maximum_queries=max_consultas,
        maximum_results=max_resultados,
    )


@mcp.tool()
async def procesar_investigacion_galiciana(
    investigation_id: str,
    paginas_por_lote: int = 5,
    presupuesto_segundos: int = 40,
    leer_adyacentes: bool = True,
) -> dict[str, Any]:
    """Lee el siguiente lote con una sesión compartida, caché, reintentos y reanudación."""
    return await get_default_engine().process(
        investigation_id,
        batch_size=paginas_por_lote,
        time_budget_seconds=presupuesto_segundos,
        read_adjacent=leer_adyacentes,
    )


@mcp.tool()
def obtener_informe_galiciana(
    investigation_id: str,
    desde: int = 0,
    max_resultados: int = 20,
    incluir_no_leidas: bool = True,
) -> dict[str, Any]:
    """Devuelve evidencias ALTO, cobertura, errores y relaciones familiares explícitas."""
    return get_default_engine().report(
        investigation_id,
        maximum_results=max_resultados,
        offset=desde,
        include_unread=incluir_no_leidas,
    )


@mcp.tool()
async def crear_investigacion_familiar_galiciana(
    investigation_id_origen: str,
    nombre_familiar: str,
    max_resultados: int = 40,
) -> dict[str, Any]:
    """Abre un expediente para un familiar que ya fue mencionado explícitamente en una fuente leída."""
    return await get_default_engine().create_family_investigation(
        investigation_id_origen,
        nombre_familiar,
        maximum_results=max_resultados,
    )


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


@mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
async def health_route(request: Request) -> JSONResponse:
    del request
    return JSONResponse(
        {
            "ok": True,
            "service": "Rob — Investigador Genealógico",
            "version": "0.7.0",
            "mcp": f"{PUBLIC_BASE_URL}/mcp",
            "openapi": f"{PUBLIC_BASE_URL}/openapi.json",
            "motor_expedientes": True,
            "persistencia": "SQLite; durable si ROB_DB_PATH apunta a un disco persistente",
        }
    )


@mcp.custom_route("/privacy", methods=["GET"], include_in_schema=False)
async def privacy_route(request: Request) -> HTMLResponse:
    del request
    return HTMLResponse(
        """<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\"><title>Privacidad — Rob</title></head>
        <body><main><h1>Privacidad — Rob</h1>
        <p>Rob recibe únicamente los datos genealógicos introducidos en la consulta para buscar en fuentes documentales públicas.</p>
        <p>No vende datos, no crea perfiles publicitarios y no solicita contraseñas. Las consultas pueden quedar temporalmente registradas por el proveedor de alojamiento para diagnóstico técnico.</p>
        <p>Las fuentes documentales consultadas pertenecen a sus respectivos organismos públicos y conservan sus propias condiciones de uso.</p>
        </main></body></html>"""
    )


@mcp.custom_route("/api/galiciana/investigar", methods=["POST"], include_in_schema=False)
async def action_investigate_galiciana(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "El cuerpo debe ser JSON válido."}, status_code=400)

    nombre = str(payload.get("nombre") or "").strip()
    if not nombre:
        return JSONResponse({"error": "Falta el campo obligatorio nombre."}, status_code=422)

    variantes = [
        str(value).strip()
        for value in payload.get("variantes", [])
        if str(value).strip()
    ]
    lugares = [
        str(value).strip()
        for value in payload.get("lugares", [])
        if str(value).strip()
    ]
    max_resultados = max(1, min(int(payload.get("max_resultados", 40)), 80))
    max_consultas = max(1, min(int(payload.get("max_consultas", 6)), 10))

    query = GenealogyQuery(
        name=nombre,
        variants=variantes,
        places=lugares,
        year_from=payload.get("fecha_desde"),
        year_to=payload.get("fecha_hasta"),
        spouse=str(payload.get("conyuge") or "").strip() or None,
        profession=str(payload.get("profesion") or "").strip() or None,
    )

    try:
        report = await GalicianaOCRConnector().investigate(
            query,
            maximum_queries=max_consultas,
            maximum_results=max_resultados,
            read_full_pages=False,
        )
    except Exception as exc:
        return JSONResponse(
            {"error": f"{type(exc).__name__}: {str(exc).strip() or repr(exc)}"},
            status_code=502,
        )

    return JSONResponse(_compact_galiciana_report(report, maximum_results=max_resultados))


@mcp.custom_route("/api/galiciana/leer-pagina", methods=["POST"], include_in_schema=False)
async def action_read_galiciana_page(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "El cuerpo debe ser JSON válido."}, status_code=400)

    page_url = str(payload.get("url_pagina") or "").strip()
    if not page_url:
        return JSONResponse({"error": "Falta el campo obligatorio url_pagina."}, status_code=422)

    terms = [
        str(value).strip()
        for value in payload.get("terminos", [])
        if str(value).strip()
    ]
    maximum_characters = max(1500, min(int(payload.get("max_caracteres", 12000)), 30000))

    try:
        result = await GalicianaOCRConnector().read_page(page_url)
    except Exception as exc:
        return JSONResponse(
            {"error": f"{type(exc).__name__}: {str(exc).strip() or repr(exc)}"},
            status_code=502,
        )

    full_text = str(result.get("texto_ocr") or "")
    contexts = _extract_contexts(full_text, terms) if terms else []
    compact = {
        "estado": result.get("estado"),
        "lectura_completa": bool(result.get("lectura_completa")),
        "url_pagina": result.get("url"),
        "url_ocr": result.get("ocr_url"),
        "url_imagen": result.get("imagen_pagina"),
        "contextos": contexts,
        "texto_ocr": "" if contexts else full_text[:maximum_characters],
        "longitud_texto_completo": len(full_text),
        "errores": result.get("errores_recuperacion", [])[-5:],
    }
    return JSONResponse(compact)


@mcp.custom_route("/api/galiciana/investigaciones/crear", methods=["POST"], include_in_schema=False)
async def action_create_investigation(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
        query = _query_from_payload(payload)
        result = await get_default_engine().create_investigation(
            query,
            maximum_queries=max(1, min(int(payload.get("max_consultas", 8)), 10)),
            maximum_results=max(1, min(int(payload.get("max_resultados", 80)), 150)),
        )
        return JSONResponse(result)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except Exception as exc:
        return JSONResponse(
            {"error": f"{type(exc).__name__}: {str(exc).strip() or repr(exc)}"},
            status_code=502,
        )


@mcp.custom_route("/api/galiciana/investigaciones/procesar", methods=["POST"], include_in_schema=False)
async def action_process_investigation(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
        investigation_id = str(payload.get("investigation_id") or "").strip()
        if not investigation_id:
            raise ValueError("Falta investigation_id.")
        result = await get_default_engine().process(
            investigation_id,
            batch_size=max(1, min(int(payload.get("paginas_por_lote", 5)), 8)),
            time_budget_seconds=max(10, min(int(payload.get("presupuesto_segundos", 40)), 55)),
            read_adjacent=bool(payload.get("leer_adyacentes", True)),
        )
        return JSONResponse(result)
    except (ValueError, KeyError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except Exception as exc:
        return JSONResponse(
            {"error": f"{type(exc).__name__}: {str(exc).strip() or repr(exc)}"},
            status_code=502,
        )


@mcp.custom_route("/api/galiciana/investigaciones/informe", methods=["POST"], include_in_schema=False)
async def action_investigation_report(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
        investigation_id = str(payload.get("investigation_id") or "").strip()
        if not investigation_id:
            raise ValueError("Falta investigation_id.")
        result = get_default_engine().report(
            investigation_id,
            maximum_results=max(1, min(int(payload.get("max_resultados", 20)), 40)),
            offset=max(0, int(payload.get("desde", 0))),
            include_unread=bool(payload.get("incluir_no_leidas", True)),
        )
        return JSONResponse(result)
    except (ValueError, KeyError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except Exception as exc:
        return JSONResponse(
            {"error": f"{type(exc).__name__}: {str(exc).strip() or repr(exc)}"},
            status_code=500,
        )


@mcp.custom_route("/api/galiciana/investigaciones/familia", methods=["POST"], include_in_schema=False)
async def action_family_investigation(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
        parent_id = str(payload.get("investigation_id_origen") or "").strip()
        relative_name = str(payload.get("nombre_familiar") or "").strip()
        if not parent_id or not relative_name:
            raise ValueError("Faltan investigation_id_origen o nombre_familiar.")
        result = await get_default_engine().create_family_investigation(
            parent_id,
            relative_name,
            maximum_results=max(1, min(int(payload.get("max_resultados", 40)), 80)),
        )
        return JSONResponse(result)
    except (ValueError, KeyError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except Exception as exc:
        return JSONResponse(
            {"error": f"{type(exc).__name__}: {str(exc).strip() or repr(exc)}"},
            status_code=502,
        )


def _openapi_schema() -> dict[str, Any]:
    person_request = {
        "type": "object",
        "required": ["nombre"],
        "properties": {
            "nombre": {"type": "string", "description": "Nombre completo principal."},
            "variantes": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "Formas sin tildes, invertidas y grafías documentadas o plausibles.",
            },
            "lugares": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "Lugares usados para distinguir homónimos, no como hechos descubiertos.",
            },
            "fecha_desde": {"type": "integer", "minimum": 1500, "maximum": 2100},
            "fecha_hasta": {"type": "integer", "minimum": 1500, "maximum": 2100},
            "conyuge": {"type": "string", "default": ""},
            "profesion": {"type": "string", "default": ""},
            "max_consultas": {"type": "integer", "minimum": 1, "maximum": 10, "default": 8},
            "max_resultados": {"type": "integer", "minimum": 1, "maximum": 150, "default": 80},
        },
        "additionalProperties": False,
    }
    process_request = {
        "type": "object",
        "required": ["investigation_id"],
        "properties": {
            "investigation_id": {"type": "string"},
            "paginas_por_lote": {"type": "integer", "minimum": 1, "maximum": 8, "default": 5},
            "presupuesto_segundos": {"type": "integer", "minimum": 10, "maximum": 55, "default": 40},
            "leer_adyacentes": {"type": "boolean", "default": True},
        },
        "additionalProperties": False,
    }
    report_request = {
        "type": "object",
        "required": ["investigation_id"],
        "properties": {
            "investigation_id": {"type": "string"},
            "desde": {"type": "integer", "minimum": 0, "default": 0},
            "max_resultados": {"type": "integer", "minimum": 1, "maximum": 40, "default": 20},
            "incluir_no_leidas": {"type": "boolean", "default": True},
        },
        "additionalProperties": False,
    }
    family_request = {
        "type": "object",
        "required": ["investigation_id_origen", "nombre_familiar"],
        "properties": {
            "investigation_id_origen": {"type": "string"},
            "nombre_familiar": {"type": "string"},
            "max_resultados": {"type": "integer", "minimum": 1, "maximum": 80, "default": 40},
        },
        "additionalProperties": False,
    }
    page_request = {
        "type": "object",
        "required": ["url_pagina"],
        "properties": {
            "url_pagina": {"type": "string", "format": "uri"},
            "terminos": {"type": "array", "items": {"type": "string"}, "default": []},
            "max_caracteres": {"type": "integer", "minimum": 1500, "maximum": 30000, "default": 12000},
        },
        "additionalProperties": False,
    }

    def operation(operation_id: str, summary: str, description: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "post": {
                "operationId": operation_id,
                "summary": summary,
                "description": description,
                "x-openai-isConsequential": False,
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": schema}},
                },
                "responses": {
                    "200": {
                        "description": "Operación completada.",
                        "content": {
    "application/json": {
        "schema": {
            "type": "object",
            "properties": {
                "estado": {
                    "type": "string",
                    "description": "Estado de la operación, cuando esté disponible.",
                },
                "investigation_id": {
                    "type": "string",
                    "description": "Identificador del expediente, cuando corresponda.",
                },
                "error": {
                    "type": "string",
                    "description": "Descripción del error, cuando se produzca.",
                },
            },
            "additionalProperties": True,
        }
    }
},
                    },
                    "422": {"description": "Datos incompletos o investigación caducada."},
                    "502": {"description": "La fuente remota no respondió correctamente."},
                },
            }
        }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Rob — Genealogista de Galiciana",
            "description": (
                "Investigaciones reanudables: búsqueda OCR, lectura ALTO por lotes, "
                "caché, contexto geométrico, páginas contiguas y familia explícita."
            ),
            "version": "0.7.0",
        },
        "servers": [{"url": PUBLIC_BASE_URL}],
        "paths": {
            "/api/galiciana/investigaciones/crear": operation(
                "crearInvestigacionGaliciana",
                "Crear una investigación reanudable en Galiciana",
                "Busca variantes, deduplica resultados y devuelve un investigation_id.",
                person_request,
            ),
            "/api/galiciana/investigaciones/procesar": operation(
                "procesarInvestigacionGaliciana",
                "Leer el siguiente lote de páginas completas",
                "Usa una sesión compartida, ALTO directo, caché, reintentos y lectura selectiva de páginas contiguas.",
                process_request,
            ),
            "/api/galiciana/investigaciones/informe": operation(
                "obtenerInformeGaliciana",
                "Obtener el expediente documental",
                "Devuelve cobertura, contextos ALTO, fuentes, errores y relaciones familiares explícitas.",
                report_request,
            ),
            "/api/galiciana/investigaciones/familia": operation(
                "crearInvestigacionFamiliarGaliciana",
                "Investigar un familiar ya documentado",
                "Solo abre la búsqueda si el parentesco fue extraído explícitamente de una página leída.",
                family_request,
            ),
            "/api/galiciana/leer-pagina": operation(
                "leerPaginaGaliciana",
                "Leer manualmente una página concreta",
                "Operación auxiliar para una URL individual de Galiciana.",
                page_request,
            ),
            "/api/galiciana/investigar": operation(
                "investigarPersonaGaliciana",
                "Búsqueda OCR rápida sin expediente",
                "Compatibilidad con la acción anterior; para investigaciones exhaustivas usa crearInvestigacionGaliciana.",
                person_request,
            ),
        },
    }


@mcp.custom_route("/openapi.json", methods=["GET"], include_in_schema=False)
async def openapi_route(request: Request) -> JSONResponse:
    del request
    return JSONResponse(_openapi_schema())


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
