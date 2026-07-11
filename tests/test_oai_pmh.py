import httpx
import pytest

from rob.connectors.oai_pmh import (
    OAIClient,
    OAIRepository,
    parse_identify,
)


IDENTIFY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <responseDate>2026-07-11T10:00:00Z</responseDate>
  <request verb="Identify">https://example.test/oai</request>
  <Identify>
    <repositoryName>Repositorio de prueba</repositoryName>
    <baseURL>https://example.test/oai</baseURL>
    <protocolVersion>2.0</protocolVersion>
    <adminEmail>archivo@example.test</adminEmail>
    <earliestDatestamp>2000-01-01</earliestDatestamp>
    <deletedRecord>persistent</deletedRecord>
    <granularity>YYYY-MM-DD</granularity>
  </Identify>
</OAI-PMH>
"""


def test_parse_identify() -> None:
    data = parse_identify(IDENTIFY_XML)
    assert data["repository_name"] == "Repositorio de prueba"
    assert data["protocol_version"] == "2.0"
    assert data["admin_emails"] == ["archivo@example.test"]


@pytest.mark.asyncio
async def test_oai_client_identify() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["verb"] == "Identify"
        return httpx.Response(200, text=IDENTIFY_XML)

    repository = OAIRepository(
        id="test",
        name="Test",
        endpoint="https://example.test/oai",
    )
    data = await OAIClient(
        repository,
        transport=httpx.MockTransport(handler),
    ).identify()

    assert data["ok"] is True
    assert data["source_id"] == "test"
