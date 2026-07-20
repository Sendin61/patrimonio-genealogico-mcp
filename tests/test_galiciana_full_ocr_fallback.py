from __future__ import annotations

from types import SimpleNamespace

import pytest

from rob.investigations.engine import UniversalInvestigationEngine
from rob.investigations.models import InvestigationTarget
from rob.investigations.sources import GalicianaSourceAdapter
from rob.investigations.store import UniversalInvestigationStore


TARGET = "Victoriano Rial Román"


class FakeGalicianaStore:
    def investigation(self, investigation_id):
        del investigation_id
        return {"diagnostics_json": "[]"}

    def query(self, investigation_id):
        del investigation_id
        return SimpleNamespace(name=TARGET, variants=[])

    def report_rows(self, investigation_id, limit, offset):
        del investigation_id, limit, offset
        return [
            {
                "status": "read",
                "full_text": (
                    "Cabecera de la página. "
                    + "x" * 900
                    + "\nSecretario, D. Victoriano Rial Román.\nFin."
                ),
            }
        ]


class FakeGalicianaEngine:
    timeout = 12
    transport = None

    def __init__(self):
        self.store = FakeGalicianaStore()

    async def create_investigation(self, query, **kwargs):
        del query, kwargs
        return {
            "investigation_id": "gal-child",
            "estado": "complete",
            "estado_busqueda": "ok",
        }

    def report(self, investigation_id, **kwargs):
        del investigation_id, kwargs
        return {
            "estado": "complete",
            "cobertura": 1.0,
            "total_resultados": 1,
            "paginas_leidas": 1,
            "paginas_pendientes": 0,
            "paginas_fallidas": 0,
            "consultas": ['"victoriano rial roman"'],
            "evidencias_documentales": [
                {
                    "fecha": "1912-02-07",
                    "titulo": "El diario de Pontevedra",
                    "url_pagina": "https://example.invalid/page",
                    "contexto": "Texto ajeno que no contiene a la persona investigada.",
                }
            ],
            "paginas_no_leidas": [],
            "familia_documentada_o_candidata": [],
            "paginacion": {
                "devueltos": 1,
                "siguiente_desde": None,
            },
        }


@pytest.mark.asyncio
async def test_universal_report_falls_back_to_persisted_full_galiciana_ocr(
    tmp_path,
) -> None:
    adapter = GalicianaSourceAdapter(FakeGalicianaEngine())
    engine = UniversalInvestigationEngine(
        UniversalInvestigationStore(str(tmp_path / "universal.sqlite3")),
        [adapter],
    )
    created = await engine.create_investigation(
        InvestigationTarget(name=TARGET),
        requested_sources=["galiciana"],
    )

    report = engine.report(
        created["investigation_id"],
        maximum_results=1,
        maximum_context_characters=180,
    )
    evidence = report["evidencias_documentales"][0]

    assert TARGET in evidence["fragmento_relevante"]
    assert "Texto ajeno" not in evidence["fragmento_relevante"]
    assert "contexto" not in evidence
    assert len(evidence["fragmento_relevante"]) <= 180
