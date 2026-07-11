import os
from typing import Any
import httpx
from mcp.server.fastmcp import FastMCP

EUROPEANA_SEARCH_URL = "https://api.europeana.eu/record/v2/search.json"
EUROPEANA_RECORD_URL = "https://api.europeana.eu/record/v2/{record_id}.json"

mcp = FastMCP("Patrimonio Genealógico MCP")

def _api_key() -> str:
    key = os.getenv("EUROPEANA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Falta EUROPEANA_API_KEY.")
    return key

def _clean_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "title": item.get("title", []),
        "creator": item.get("dcCreator", []),
        "date": item.get("year", []) or item.get("dcDate", []),
        "description": item.get("dcDescription", []),
        "place": item.get("edmPlaceLabel", []),
        "provider": item.get("dataProvider", []),
        "country": item.get("country", []),
        "type": item.get("type"),
        "rights": item.get("rights", []),
        "preview": item.get("edmPreview", []),
        "source_url": item.get("guid"),
    }

@mcp.tool()
async def buscar_europeana(consulta: str, filas: int = 20, inicio: int = 1) -> dict[str, Any]:
    filas = max(1, min(filas, 100))
    inicio = max(1, inicio)
    params = {
        "wskey": _api_key(),
        "query": consulta,
        "rows": filas,
        "start": inicio,
        "profile": "rich",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(EUROPEANA_SEARCH_URL, params=params)
        response.raise_for_status()
        data = response.json()
    return {
        "consulta": consulta,
        "total": data.get("totalResults", 0),
        "inicio": inicio,
        "resultados": [_clean_item(item) for item in data.get("items", [])],
    }

@mcp.tool()
async def buscar_persona(
    nombre: str,
    lugar: str = "",
    fecha_desde: int | None = None,
    fecha_hasta: int | None = None,
    filas: int = 30,
) -> dict[str, Any]:
    partes = [f'"{nombre}"']
    if lugar.strip():
        partes.append(f'"{lugar.strip()}"')
    if fecha_desde is not None or fecha_hasta is not None:
        desde = fecha_desde if fecha_desde is not None else 1
        hasta = fecha_hasta if fecha_hasta is not None else 2100
        partes.append(f"YEAR:[{desde} TO {hasta}]")
    return await buscar_europeana(" AND ".join(partes), filas=filas)

@mcp.tool()
async def abrir_registro_europeana(record_id: str) -> dict[str, Any]:
    if not record_id.startswith("/"):
        record_id = "/" + record_id
    url = EUROPEANA_RECORD_URL.format(record_id=record_id)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, params={"wskey": _api_key()})
        response.raise_for_status()
        return response.json()

@mcp.tool()
def estado() -> dict[str, Any]:
    return {
        "servidor": "Patrimonio Genealógico MCP",
        "version": "0.1.0",
        "fuentes_activas": ["Europeana"],
        "europeana_configurada": bool(os.getenv("EUROPEANA_API_KEY", "").strip()),
    }

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
