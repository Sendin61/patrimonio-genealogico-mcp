import httpx
import pytest

from rob.connectors.europeana_galicia import (
    EuropeanaGaliciaConnector,
    GALICIANA_BDG_COLLECTION,
    GALICIANA_DATA_PROVIDER,
    build_europeana_query,
)
from rob.models import GenealogyQuery


def test_build_europeana_query_contains_variants() -> None:
    query = GenealogyQuery(
        name="Manuel Pérez Eiriz",
        variants=["Pérez Eiriz, Manuel"],
    )
    built = build_europeana_query(query)

    assert '"Manuel Pérez Eiriz"' in built
    assert '"Manuel Perez Eiriz"' in built
    assert " OR " in built
    assert " AND " in built


@pytest.mark.asyncio
async def test_galicia_connector_filters_exact_provider_and_collection() -> None:
    payload = {
        "totalResults": 1,
        "items": [
            {
                "id": "/2022706/example",
                "title": ["Manuel Pérez Eiriz"],
                "dcCreator": ["Pérez Eiriz, Manuel"],
                "year": ["1879"],
                "dataProvider": [GALICIANA_DATA_PROVIDER],
                "provider": ["Hispana"],
                "guid": "https://www.europeana.eu/item/2022706/example",
                "type": "TEXT",
            }
        ],
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        qf_values = request.url.params.get_list("qf")
        assert f'DATA_PROVIDER:"{GALICIANA_DATA_PROVIDER}"' in qf_values
        assert f'europeana_collectionName:"{GALICIANA_BDG_COLLECTION}"' in qf_values
        assert request.url.params["profile"] == "rich"
        return httpx.Response(200, json=payload)

    connector = EuropeanaGaliciaConnector(
        "test-key",
        transport=httpx.MockTransport(handler),
    )
    report = await connector.search(
        GenealogyQuery(
            name="Manuel Pérez Eiriz",
            variants=["Pérez Eiriz, Manuel"],
            places=["Lugo"],
            year_from=1840,
            year_to=1910,
        )
    )

    assert report.status == "ok"
    assert report.total_api == 1
    assert len(report.results) == 1
    assert report.results[0].source_id == "galiciana_europeana"
    assert report.results[0].score >= 0.8


@pytest.mark.asyncio
async def test_galicia_connector_reports_api_failures() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="temporarily unavailable")

    connector = EuropeanaGaliciaConnector(
        "test-key",
        transport=httpx.MockTransport(handler),
    )
    report = await connector.search(GenealogyQuery(name="Persona de prueba"))

    assert report.status == "unavailable"
    assert report.error_type == "HTTPStatusError"
    assert report.results == []
