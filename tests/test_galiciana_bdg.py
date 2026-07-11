import json

import httpx
import pytest

from rob.connectors.galiciana_bdg import (
    GalicianaBDGConnector,
    build_person_sparql,
)
from rob.models import GenealogyQuery


def test_build_person_sparql_escapes_input_and_dates() -> None:
    query = GenealogyQuery(
        name='Manuel "Pérez" Eiriz',
        places=["Merlán"],
        year_from=1830,
        year_to=1910,
    )
    sparql = build_person_sparql(query, limit=500)

    assert '\\"' not in sparql  # las comillas se eliminan al compactar variantes
    assert "1830" in sparql
    assert "1910" in sparql
    assert "LIMIT 100" in sparql
    assert "urn:galiciana:objetos-digitales" in sparql


@pytest.mark.asyncio
async def test_connector_parses_sparql_json() -> None:
    payload = {
        "head": {"vars": ["obra", "titulo", "autor", "fecha", "shownAt"]},
        "results": {
            "bindings": [
                {
                    "obra": {"type": "uri", "value": "https://example.test/obra/1"},
                    "titulo": {"type": "literal", "value": "Manuel Pérez Eiriz"},
                    "autor": {"type": "literal", "value": "Pérez Eiriz, Manuel"},
                    "fecha": {"type": "literal", "value": "1879"},
                    "shownAt": {"type": "uri", "value": "https://example.test/registro/1"},
                }
            ]
        },
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        assert "query" in request.url.params
        return httpx.Response(200, json=payload)

    connector = GalicianaBDGConnector(
        transport=httpx.MockTransport(handler)
    )
    results = await connector.search(
        GenealogyQuery(name="Manuel Pérez Eiriz"),
        limit=10,
    )

    assert len(results) == 1
    assert results[0].url == "https://example.test/registro/1"
    assert results[0].score >= 0.8
