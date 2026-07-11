import httpx
import pytest

from rob.connectors.galiciana_bdg import (
    GalicianaBDGConnector,
    build_person_sparql,
    build_term_sparql,
)
from rob.models import GenealogyQuery


def test_build_person_sparql_is_compact_and_has_dates() -> None:
    query = GenealogyQuery(
        name='Manuel "Pérez" Eiriz',
        variants=["Pérez Eiriz, Manuel", "Manuel Perez Eiriz"],
        places=["Merlán", "Chantada", "Lugo"],
        year_from=1830,
        year_to=1910,
    )
    sparql = build_person_sparql(query, limit=500)

    assert "1830" in sparql
    assert "1910" in sparql
    assert "LIMIT 100" in sparql
    assert "urn:galiciana:objetos-digitales" in sparql
    assert len(sparql) < 1400


def test_build_term_sparql_uses_one_term() -> None:
    query = GenealogyQuery(name="Manuel Pérez Eiriz")
    sparql = build_term_sparql(query, "manuel pérez eiriz", limit=20)

    assert "manuel pérez eiriz" in sparql
    assert "VALUES" not in sparql
    assert len(sparql) < 1200


@pytest.mark.asyncio
async def test_connector_uses_short_get_requests_and_deduplicates() -> None:
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
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert "query" in request.url.params
        assert len(str(request.url)) < 1900
        return httpx.Response(200, json=payload)

    connector = GalicianaBDGConnector(
        transport=httpx.MockTransport(handler)
    )
    results = await connector.search(
        GenealogyQuery(
            name="Manuel Pérez Eiriz",
            variants=["Manuel Perez Eiriz", "Pérez Eiriz, Manuel"],
            places=["Merlán", "Chantada", "Lugo"],
            spouse="Ramona Sindín",
        ),
        limit=20,
    )

    assert 1 < len(requests) <= 4
    assert len(results) == 1
    assert results[0].url == "https://example.test/registro/1"
    assert results[0].score >= 0.8
