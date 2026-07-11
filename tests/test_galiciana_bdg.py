import httpx
import pytest
from rdflib.plugins.sparql.parser import parseQuery

from rob.connectors.galiciana_bdg import (
    GalicianaBDGConnector,
    build_field_sparql,
)
from rob.models import GenealogyQuery


@pytest.mark.parametrize("field_name", ["author", "title", "description"])
def test_queries_follow_official_field_patterns(field_name: str) -> None:
    sparql = build_field_sparql(
        "manuel pérez eiriz",
        field_name,  # type: ignore[arg-type]
        limit=20,
    )

    parseQuery(sparql)
    assert "GRAPH <" not in sparql
    assert "CONCAT(" not in sparql
    assert "default-graph-uri" not in sparql
    assert len(sparql) < 1500


@pytest.mark.asyncio
async def test_connector_sends_default_graph_and_returns_results() -> None:
    payload = {
        "head": {"vars": ["obra", "titulo", "autor", "fecha", "shownAt"]},
        "results": {
            "bindings": [
                {
                    "obra": {"type": "uri", "value": "https://example.test/obra/1"},
                    "titulo": {"type": "literal", "value": "Manuel Pérez Eiriz"},
                    "autor": {"type": "literal", "value": "Pérez Eiriz, Manuel"},
                    "fecha": {"type": "literal", "value": "1879"},
                    "shownAt": {
                        "type": "uri",
                        "value": "https://example.test/registro/1",
                    },
                }
            ]
        },
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.params["default-graph-uri"] == (
            "urn:galiciana:objetos-digitales"
        )
        assert "query" in request.url.params
        return httpx.Response(200, json=payload)

    connector = GalicianaBDGConnector(
        transport=httpx.MockTransport(handler),
    )
    report = await connector.search_with_diagnostics(
        GenealogyQuery(
            name="Manuel Pérez Eiriz",
            variants=["Pérez Eiriz, Manuel"],
            year_from=1840,
            year_to=1910,
        ),
        limit=20,
    )

    assert report.status == "ok"
    assert report.successful_requests > 0
    assert report.failed_requests == 0
    assert len(report.results) == 1
    assert report.results[0].url == "https://example.test/registro/1"


@pytest.mark.asyncio
async def test_timeout_is_reported_instead_of_blank_tool_failure() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("", request=request)
        return httpx.Response(
            200,
            json={"head": {"vars": []}, "results": {"bindings": []}},
        )

    connector = GalicianaBDGConnector(
        transport=httpx.MockTransport(handler),
    )
    report = await connector.search_with_diagnostics(
        GenealogyQuery(name="Manuel Pérez Eiriz"),
        limit=20,
    )

    assert report.status == "partial"
    assert report.failed_requests == 1
    assert report.successful_requests > 0
    failed = next(item for item in report.diagnostics if not item.ok)
    assert failed.error_type == "ReadTimeout"
    assert failed.error
