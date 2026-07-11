from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree

import httpx


OAI_NS = "http://www.openarchives.org/OAI/2.0/"
NS = {"oai": OAI_NS}


class OAIError(RuntimeError):
    """Error devuelto por un repositorio OAI-PMH."""


@dataclass(frozen=True, slots=True)
class OAIRepository:
    id: str
    name: str
    endpoint: str


GALICIANA_BDG_OAI = OAIRepository(
    id="galiciana_bdg",
    name="Galiciana. Biblioteca Dixital de Galicia",
    endpoint="https://biblioteca.galiciana.gal/es/oai/oai.do",
)

GALICIANA_ADG_OAI = OAIRepository(
    id="galiciana_adg",
    name="Galiciana. Arquivo Dixital de Galicia",
    endpoint="https://arquivo.galiciana.gal/arpadweb/es/oai/oai.do",
)


def _text(element: ElementTree.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


def parse_identify(xml_text: str) -> dict[str, Any]:
    root = ElementTree.fromstring(xml_text)

    error = root.find("oai:error", NS)
    if error is not None:
        code = error.attrib.get("code", "unknown")
        raise OAIError(f"{code}: {_text(error) or 'Error OAI-PMH'}")

    identify = root.find("oai:Identify", NS)
    if identify is None:
        raise OAIError("La respuesta no contiene el bloque Identify.")

    admin_emails = [
        value
        for node in identify.findall("oai:adminEmail", NS)
        if (value := _text(node))
    ]
    compressions = [
        value
        for node in identify.findall("oai:compression", NS)
        if (value := _text(node))
    ]

    return {
        "repository_name": _text(identify.find("oai:repositoryName", NS)),
        "base_url": _text(identify.find("oai:baseURL", NS)),
        "protocol_version": _text(identify.find("oai:protocolVersion", NS)),
        "earliest_datestamp": _text(identify.find("oai:earliestDatestamp", NS)),
        "deleted_record": _text(identify.find("oai:deletedRecord", NS)),
        "granularity": _text(identify.find("oai:granularity", NS)),
        "admin_emails": admin_emails,
        "compressions": compressions,
    }


class OAIClient:
    def __init__(
        self,
        repository: OAIRepository,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.repository = repository
        self.timeout = timeout
        self.transport = transport

    async def identify(self) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            transport=self.transport,
            headers={"User-Agent": "RobGenealogia/0.3"},
        ) as client:
            response = await client.get(
                self.repository.endpoint,
                params={"verb": "Identify"},
            )
            response.raise_for_status()

        data = parse_identify(response.text)
        data.update(
            {
                "source_id": self.repository.id,
                "source_name": self.repository.name,
                "requested_endpoint": self.repository.endpoint,
                "ok": True,
            }
        )
        return data
