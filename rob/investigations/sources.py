from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Protocol, runtime_checkable

from rob.connectors.galiciana_ocr import GalicianaOCRConnector
from rob.galiciana_investigations import GalicianaInvestigationEngine

from .models import InvestigationTarget, SourceCapabilities


@runtime_checkable
class GenealogicalSourceAdapter(Protocol):
    """Source-neutral contract implemented by genealogy providers."""

    source_name: str
    capabilities: SourceCapabilities

    @property
    def available(self) -> bool: ...

    async def create_investigation(
        self,
        target: InvestigationTarget,
        *,
        maximum_queries: int,
        maximum_results: int,
    ) -> dict[str, Any]: ...

    async def process_next_batch(
        self,
        source_investigation_id: str,
        *,
        batch_size: int,
        time_budget_seconds: int,
    ) -> dict[str, Any]: ...

    def get_report(
        self,
        source_investigation_id: str,
        *,
        offset: int,
        maximum_results: int,
        include_pending: bool,
    ) -> dict[str, Any]: ...

    async def read_source(
        self,
        source_url: str,
        *,
        terms: list[str],
        maximum_characters: int,
    ) -> dict[str, Any]: ...


def _normalise(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _contexts(text: str, terms: list[str], maximum: int = 8) -> list[str]:
    normalised = _normalise(text)
    spans: list[tuple[int, int]] = []
    for term in terms:
        wanted = _normalise(term.strip())
        if not wanted:
            continue
        start = 0
        while len(spans) < maximum * 3:
            found = normalised.find(wanted, start)
            if found < 0:
                break
            spans.append((max(0, found - 900), min(len(text), found + len(wanted) + 900)))
            start = found + max(1, len(wanted))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1] + 120:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return [
        re.sub(r"\s+", " ", text[start:end]).strip()
        for start, end in merged[:maximum]
        if text[start:end].strip()
    ]


class GalicianaSourceAdapter:
    source_name = "galiciana"
    capabilities = SourceCapabilities()

    def __init__(
        self,
        engine: GalicianaInvestigationEngine,
        *,
        connector: GalicianaOCRConnector | None = None,
    ) -> None:
        self.engine = engine
        self.connector = connector or GalicianaOCRConnector(
            timeout=engine.timeout, transport=engine.transport
        )

    @property
    def available(self) -> bool:
        return True

    def _diagnostics(self, source_investigation_id: str) -> list[dict[str, Any]]:
        row = self.engine.store.investigation(source_investigation_id)
        if row is None:
            return []
        try:
            value = json.loads(row.get("diagnostics_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
        return value if isinstance(value, list) else []

    async def create_investigation(
        self,
        target: InvestigationTarget,
        *,
        maximum_queries: int,
        maximum_results: int,
    ) -> dict[str, Any]:
        result = await self.engine.create_investigation(
            target.to_genealogy_query(),
            maximum_queries=maximum_queries,
            maximum_results=maximum_results,
        )
        source_id = str(result["investigation_id"])
        status = "processing"
        if result.get("estado") == "complete":
            status = "failed" if result.get("estado_busqueda") == "unavailable" else "complete"
        return {
            "source_investigation_id": source_id,
            "status": status,
            "diagnostics": self._diagnostics(source_id),
            "detail": result,
        }

    async def process_next_batch(
        self,
        source_investigation_id: str,
        *,
        batch_size: int,
        time_budget_seconds: int,
    ) -> dict[str, Any]:
        result = await self.engine.process(
            source_investigation_id,
            batch_size=batch_size,
            time_budget_seconds=time_budget_seconds,
        )
        return {
            "status": "complete" if result.get("completada") else "processing",
            "complete": bool(result.get("completada")),
            "detail": result,
        }

    def get_report(
        self,
        source_investigation_id: str,
        *,
        offset: int,
        maximum_results: int,
        include_pending: bool,
    ) -> dict[str, Any]:
        report = self.engine.report(
            source_investigation_id,
            offset=offset,
            maximum_results=maximum_results,
            include_unread=include_pending,
        )
        report["diagnosticos_fuente"] = self._diagnostics(source_investigation_id)
        return report

    async def read_source(
        self,
        source_url: str,
        *,
        terms: list[str],
        maximum_characters: int,
    ) -> dict[str, Any]:
        detail = await self.connector.read_page(source_url)
        full_text = str(detail.get("texto_ocr") or "")
        contexts = _contexts(full_text, terms) if terms else []
        return {
            "fuente": self.source_name,
            "estado": detail.get("estado"),
            "source_url": detail.get("url") or source_url,
            "content": "" if contexts else full_text[:maximum_characters],
            "contextos": contexts,
            "documento": {
                "url_ocr": detail.get("ocr_url"),
                "url_imagen": detail.get("imagen_pagina"),
                "longitud_contenido": len(full_text),
            },
            "detalle_fuente": detail,
        }
