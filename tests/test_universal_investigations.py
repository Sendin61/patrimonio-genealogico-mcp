from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace
from typing import Any

import pytest

from rob.investigations.engine import (
    COMPACT_REPORT_TEXT_LIMIT,
    COMPLETE_REPORT_TEXT_LIMIT,
    UniversalInvestigationEngine,
    _document_facts,
    _is_structural_text,
)
from rob.investigations.models import InvestigationTarget, SourceCapabilities
from rob.investigations.sources import GalicianaSourceAdapter
from rob.investigations.store import UniversalInvestigationStore


class FakeSourceAdapter:
    capabilities = SourceCapabilities()
    available = True

    def __init__(
        self,
        source_name: str = "galiciana",
        *,
        fail_create: bool = False,
        fail_process: bool = False,
        complete_on_create: bool = False,
    ) -> None:
        self.source_name = source_name
        self.fail_create = fail_create
        self.fail_process = fail_process
        self.complete_on_create = complete_on_create
        self.created = 0
        self.processed = 0

    async def create_investigation(self, target, **kwargs) -> dict[str, Any]:
        del target, kwargs
        self.created += 1
        if self.fail_create:
            raise RuntimeError(f"{self.source_name} no disponible")
        return {
            "source_investigation_id": f"{self.source_name}-child-{self.created}",
            "status": "complete" if self.complete_on_create else "processing",
        }

    async def process_next_batch(self, source_investigation_id, **kwargs):
        del source_investigation_id, kwargs
        self.processed += 1
        if self.fail_process:
            raise RuntimeError(f"{self.source_name} falló al procesar")
        return {
            "status": "complete",
            "complete": True,
            "detail": {"procesadas_en_esta_llamada": 1},
        }

    def get_report(self, source_investigation_id, **kwargs):
        del kwargs
        return {
            "estado": "complete",
            "cobertura": 1.0,
            "total_resultados": 1,
            "paginas_leidas": 1,
            "paginas_pendientes": 0,
            "paginas_fallidas": 0,
            "evidencias_documentales": [
                {
                    "fecha": "1911-08-25",
                    "titulo": "Noticiero de Vigo",
                    "url_pagina": "https://example.invalid/page",
                    "contexto": "OCR literal",
                }
            ],
            "paginas_no_leidas": [],
            "familia_documentada_o_candidata": [
                {"relation_type": "cónyuge", "relative_name": "Raquel"}
            ],
            "source_investigation_id": source_investigation_id,
        }

    async def read_source(self, source_url, **kwargs):
        del kwargs
        return {"fuente": self.source_name, "source_url": source_url, "content": "OCR"}


def make_engine(tmp_path, *adapters):
    store = UniversalInvestigationStore(str(tmp_path / "universal.sqlite3"))
    return UniversalInvestigationEngine(store, adapters), store


def budgeted_evidence_text(value: Any, key: str = "") -> int:
    if isinstance(value, str):
        return 0 if _is_structural_text(key) else len(value)
    if isinstance(value, list):
        return sum(budgeted_evidence_text(item, key) for item in value)
    if isinstance(value, dict):
        return sum(
            budgeted_evidence_text(item, str(child_key))
            for child_key, item in value.items()
        )
    return 0


@pytest.mark.asyncio
async def test_create_persist_and_recover_universal_and_child_ids(tmp_path) -> None:
    adapter = FakeSourceAdapter()
    engine, store = make_engine(tmp_path, adapter)
    created = await engine.create_investigation(
        InvestigationTarget(name="Andrés Fernández Táboas"),
        requested_sources=["galiciana", "galiciana"],
    )

    universal_id = created["investigation_id"]
    child_id = created["fuentes"][0]["investigation_id_fuente"]
    assert universal_id != child_id
    assert created["fuentes_solicitadas"] == ["galiciana"]
    assert adapter.created == 1

    recovered = UniversalInvestigationStore(str(tmp_path / "universal.sqlite3"))
    assert recovered.require(universal_id)["target"].name == "Andrés Fernández Táboas"
    runs = recovered.source_runs(universal_id)
    assert len(runs) == 1
    assert runs[0]["source_investigation_id"] == child_id
    assert recovered.ensure_source_run(universal_id, "galiciana")["id"] == runs[0]["id"]

    with sqlite3.connect(tmp_path / "universal.sqlite3") as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"universal_investigations", "universal_source_runs"} <= tables


@pytest.mark.asyncio
async def test_process_and_aggregate_source_report(tmp_path) -> None:
    adapter = FakeSourceAdapter()
    engine, _ = make_engine(tmp_path, adapter)
    created = await engine.create_investigation(
        InvestigationTarget(name="Andrés Fernández Táboas")
    )
    universal_id = created["investigation_id"]

    processed = await engine.process(universal_id, batch_size=3, time_budget_seconds=55)
    assert processed["estado"] == "complete"
    assert processed["completada"] is True
    assert adapter.processed == 1

    report = engine.report(universal_id, mode="completo", include_context=True)
    evidence = report["evidencias_documentales"][0]
    assert evidence["fuente"] == "galiciana"
    assert evidence["investigation_id_fuente"].startswith("galiciana-child-")
    assert evidence["contexto"] == "OCR literal"
    assert report["relaciones_familiares_documentadas"][0]["fuente"] == "galiciana"
    assert report["cobertura_por_fuente"][0]["cobertura"] == 1.0


@pytest.mark.asyncio
async def test_unknown_source_is_rejected_before_persistence(tmp_path) -> None:
    engine, store = make_engine(tmp_path, FakeSourceAdapter())
    with pytest.raises(ValueError, match="Fuente desconocida"):
        await engine.create_investigation(
            InvestigationTarget(name="Persona"), requested_sources=["exa"]
        )
    with sqlite3.connect(tmp_path / "universal.sqlite3") as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM universal_investigations"
        ).fetchone()[0]
    assert count == 0
    assert store.source_runs("missing") == []


@pytest.mark.asyncio
async def test_source_creation_failure_preserves_dossier(tmp_path) -> None:
    failing = FakeSourceAdapter("fallida", fail_create=True)
    engine, store = make_engine(tmp_path, failing)
    created = await engine.create_investigation(
        InvestigationTarget(name="Persona"),
        requested_sources=["fallida"],
    )

    assert created["estado"] == "failed"
    assert store.require(created["investigation_id"])["status"] == "failed"
    runs = {run["source_name"]: run for run in store.source_runs(created["investigation_id"])}
    assert runs["fallida"]["status"] == "failed"
    assert "no disponible" in runs["fallida"]["diagnostics"][0]["mensaje"]


@pytest.mark.asyncio
async def test_partial_when_one_source_completes_and_another_fails(tmp_path) -> None:
    complete = FakeSourceAdapter("documentada")
    failing = FakeSourceAdapter("fallida", fail_process=True)
    engine, store = make_engine(tmp_path, complete, failing)
    created = await engine.create_investigation(
        InvestigationTarget(name="Persona"),
        requested_sources=["documentada", "fallida"],
    )
    processed = await engine.process(created["investigation_id"])

    assert processed["estado"] == "partial"
    assert store.require(created["investigation_id"])["status"] == "partial"
    runs = {run["source_name"]: run for run in store.source_runs(created["investigation_id"])}
    assert runs["documentada"]["status"] == "complete"
    assert runs["fallida"]["status"] == "failed"
    assert "falló al procesar" in runs["fallida"]["diagnostics"][0]["mensaje"]


@pytest.mark.asyncio
async def test_process_uses_one_global_deadline_and_leaves_unstarted_sources_pending(
    tmp_path,
) -> None:
    class SlowSource(FakeSourceAdapter):
        async def process_next_batch(self, source_investigation_id, **kwargs):
            del source_investigation_id, kwargs
            self.processed += 1
            await asyncio.sleep(0.2)
            return {"status": "complete", "complete": True}

    adapters = [SlowSource(f"source-{index}") for index in range(3)]
    engine, store = make_engine(tmp_path, *adapters)
    created = await engine.create_investigation(
        InvestigationTarget(name="Persona"),
        requested_sources=[adapter.source_name for adapter in adapters],
    )

    loop = asyncio.get_running_loop()
    started = loop.time()
    result = await engine.process(
        created["investigation_id"], time_budget_seconds=0.03
    )
    elapsed = loop.time() - started

    assert elapsed < 0.12
    assert result["estado"] == "processing"
    assert adapters[0].processed == 1
    assert [adapter.processed for adapter in adapters[1:]] == [0, 0]
    runs = store.source_runs(created["investigation_id"])
    assert [run["status"] for run in runs] == ["pending", "pending", "pending"]
    assert runs[1]["diagnostics"][-1]["operacion"] == (
        "presupuesto_agotado_antes_de_iniciar"
    )
    assert "antes de iniciar" in runs[1]["diagnostics"][-1]["mensaje"]


@pytest.mark.asyncio
async def test_report_applies_one_deterministic_global_pagination(tmp_path) -> None:
    class PaginatedSource(FakeSourceAdapter):
        def __init__(self, source_name, evidence_count, pending_count):
            super().__init__(source_name, complete_on_create=True)
            self.evidence = [
                {
                    "fecha": f"1900-01-{index + 1:02d}",
                    "titulo": f"{source_name}-evidence-{index}",
                    "url_pagina": f"https://example.invalid/{source_name}/e/{index}",
                    "contexto": f"context-{source_name}-{index}",
                }
                for index in range(evidence_count)
            ]
            self.pending = [
                {
                    "titulo": f"{source_name}-pending-{index}",
                    "url_pagina": f"https://example.invalid/{source_name}/p/{index}",
                }
                for index in range(pending_count)
            ]
            self.report_offsets = []

        def get_report(self, source_investigation_id, **kwargs):
            offset = kwargs["offset"]
            limit = kwargs["maximum_results"]
            self.report_offsets.append(offset)
            rows = [
                ("evidence", item) for item in self.evidence
            ] + [("pending", item) for item in self.pending]
            page = rows[offset : offset + limit]
            return {
                "cobertura": 1.0,
                "total_resultados": len(rows),
                "paginas_leidas": len(self.evidence),
                "paginas_pendientes": len(self.pending),
                "paginas_fallidas": 0,
                "evidencias_documentales": [
                    item for kind, item in page if kind == "evidence"
                ],
                "paginas_no_leidas": [
                    item for kind, item in page if kind == "pending"
                ],
                "familia_documentada_o_candidata": [],
                "paginacion": {
                    "devueltos": len(page),
                    "siguiente_desde": (
                        offset + len(page)
                        if offset + len(page) < len(rows)
                        else None
                    ),
                },
                "source_investigation_id": source_investigation_id,
            }

    first = PaginatedSource("first", 4, 2)
    second = PaginatedSource("second", 3, 3)
    engine, _ = make_engine(tmp_path, first, second)

    created = await engine.create_investigation(
        InvestigationTarget(name="Persona"),
        requested_sources=["first", "second"],
    )
    investigation_id = created["investigation_id"]
    page_one = engine.report(investigation_id, offset=0, maximum_results=3)
    page_two = engine.report(investigation_id, offset=3, maximum_results=3)
    page_three = engine.report(investigation_id, offset=6, maximum_results=3)

    titles_one = [item["titulo"] for item in page_one["evidencias_documentales"]]
    titles_two = [item["titulo"] for item in page_two["evidencias_documentales"]]
    titles_three = [item["titulo"] for item in page_three["evidencias_documentales"]]
    assert titles_one == [f"first-evidence-{index}" for index in range(3)]
    assert titles_two == [
        "first-evidence-3",
        "second-evidence-0",
        "second-evidence-1",
    ]
    assert titles_three == ["second-evidence-2"]
    assert len(set(titles_one + titles_two + titles_three)) == 7
    assert page_one["paginacion"]["siguiente_desde"] == 3
    assert page_two["paginacion"]["siguiente_desde"] == 6
    assert page_three["paginacion"]["siguiente_desde"] is None
    assert page_three["paginacion"]["total_evidencias"] == 7
    assert first.report_offsets[0] == second.report_offsets[0] == 0
    assert first.report_offsets.count(0) == second.report_offsets.count(0) == 3
    assert all(
        item["fuente"] and item["investigation_id_fuente"]
        for item in page_two["evidencias_documentales"]
    )
    assert [
        item["titulo"] for item in page_one["elementos_pendientes"]
    ] == ["first-pending-0", "first-pending-1", "second-pending-0"]
    assert [
        item["titulo"] for item in page_two["elementos_pendientes"]
    ] == ["second-pending-1", "second-pending-2"]
    assert page_one["paginacion"]["siguiente_desde_pendientes"] == 3
    assert page_two["paginacion"]["siguiente_desde_pendientes"] is None


def test_document_facts_only_use_windows_local_to_target_name() -> None:
    terms = ["Persona Objetivo", "P. Objetivo"]

    categories, facts = _document_facts(
        "Persona Objetivo figura en la lista.\n"
        + "Otra sección completamente distinta: Manuel García, alcalde.",
        terms,
    )
    assert "cargo público" not in categories
    assert facts == []

    categories, facts = _document_facts(
        "En la relación consta Persona Objetivo, alcalde de la localidad.", terms
    )
    assert "cargo público" in categories
    assert all(
        "Persona Objetivo" in fact["texto_soporte"]
        and "alcalde" in fact["texto_soporte"]
        and len(fact["texto_soporte"]) <= 320
        for fact in facts
        if fact["tipo"] == "cargo público"
    )

    categories, facts = _document_facts(
        "Álvaro Núñez, alcalde del municipio, tomó posesión.", ["Alvaro Nunez"]
    )
    assert "cargo público" in categories
    assert "Álvaro Núñez" in facts[0]["texto_soporte"]


def test_explicit_kinship_requires_target_and_indicator_in_same_local_unit() -> None:
    terms = ["Persona Objetivo"]
    categories, facts = _document_facts(
        "Manuel García, hijo de José García. Persona Objetivo figura en la lista.",
        terms,
    )
    assert "parentesco explícito" not in categories
    assert facts == []

    categories, facts = _document_facts(
        "Persona Objetivo, hijo de Manuel Objetivo, comparece en el padrón.", terms
    )
    kinship = [fact for fact in facts if fact["tipo"] == "parentesco explícito"]
    assert "parentesco explícito" in categories
    assert len(kinship) == 1
    assert "Persona Objetivo" in kinship[0]["texto_soporte"]
    assert "hijo de" in kinship[0]["texto_soporte"]


@pytest.mark.asyncio
async def test_existing_strong_category_does_not_gain_nominal_mention(tmp_path) -> None:
    class CategorisedSource(FakeSourceAdapter):
        def get_report(self, source_investigation_id, **kwargs):
            report = super().get_report(source_investigation_id, **kwargs)
            report["evidencias_documentales"][0]["categorias"] = ["propiedad"]
            report["evidencias_documentales"][0]["contexto"] = (
                "Texto documental sin el nombre buscado."
            )
            return report

    engine, _ = make_engine(tmp_path, CategorisedSource(complete_on_create=True))
    created = await engine.create_investigation(
        InvestigationTarget(name="Persona Objetivo")
    )
    evidence = engine.report(created["investigation_id"])[
        "evidencias_documentales"
    ][0]

    assert evidence["categorias"] == ["propiedad"]
    assert evidence["hechos_extraidos"] == []


@pytest.mark.asyncio
async def test_compact_report_bounds_one_hundred_ocr_evidences(tmp_path) -> None:
    class LargeReportSource(FakeSourceAdapter):
        def __init__(self):
            super().__init__(complete_on_create=True)
            self.rows = [
                {
                    "fecha": "1910-01-01",
                    "publicacion": "Boletín",
                    "titulo": f"Documento {index}",
                    "pagina": str(index + 1),
                    "puntuacion": 0.9,
                    "categorias": [],
                    "url_pagina": f"https://example.invalid/{index}",
                    "url_ocr": f"https://example.invalid/{index}/alto",
                    "url_imagen": f"https://example.invalid/{index}/image",
                    "contexto": (
                        "X" * 4_000
                        + " Persona Objetivo fue nombrado alcalde, propietario, "
                        + "residente, comerciante, demandado y candidato "
                        + "Y" * 4_000
                    ),
                    "texto_ocr": "OCR" * 10_000,
                    "texto_visible": "VISIBLE" * 10_000,
                }
                for index in range(100)
            ]

        def get_report(self, source_investigation_id, **kwargs):
            offset = kwargs["offset"]
            page = self.rows[offset : offset + kwargs["maximum_results"]]
            return {
                "cobertura": 1.0,
                "total_resultados": 100,
                "paginas_leidas": 100,
                "paginas_pendientes": 0,
                "paginas_fallidas": 0,
                "evidencias_documentales": page,
                "paginas_no_leidas": [],
                "familia_documentada_o_candidata": [],
                "paginacion": {
                    "devueltos": len(page),
                    "siguiente_desde": (
                        offset + len(page) if offset + len(page) < 100 else None
                    ),
                },
                "source_investigation_id": source_investigation_id,
            }

    engine, _ = make_engine(tmp_path, LargeReportSource())
    created = await engine.create_investigation(
        InvestigationTarget(name="Persona Objetivo")
    )
    report = engine.report(
        created["investigation_id"], maximum_results=100, maximum_context_characters=800
    )

    assert len(report["evidencias_documentales"]) == 100
    assert len(json.dumps(report, ensure_ascii=False)) < 150_000
    assert (
        sum(
            budgeted_evidence_text(item)
            for item in report["evidencias_documentales"]
        )
        <= COMPACT_REPORT_TEXT_LIMIT
    )
    assert all(
        "texto_ocr" not in item and "texto_visible" not in item and "contexto" not in item
        for item in report["evidencias_documentales"]
    )
    assert all(
        len(item["fragmento_relevante"]) <= 800
        for item in report["evidencias_documentales"]
    )
    assert (
        "Persona Objetivo"
        in report["evidencias_documentales"][0]["fragmento_relevante"]
    )
    assert report["texto_truncado"] is True
    assert report["limite_texto"] == COMPACT_REPORT_TEXT_LIMIT
    last = report["evidencias_documentales"][-1]
    assert last["url_pagina"].endswith("/99")
    assert last["url_ocr"].endswith("/99/alto")
    assert last["url_imagen"].endswith("/99/image")
    assert last["investigation_id_fuente"]
    assert report["paginacion"]["total_evidencias"] == 100

    facts_only = engine.report(
        created["investigation_id"],
        maximum_results=100,
        maximum_context_characters=0,
    )
    assert (
        sum(
            budgeted_evidence_text(item)
            for item in facts_only["evidencias_documentales"]
        )
        <= COMPACT_REPORT_TEXT_LIMIT
    )
    assert facts_only["texto_truncado"] is True
    assert any(
        not fact["texto_soporte"]
        for item in facts_only["evidencias_documentales"]
        for fact in item["hechos_extraidos"]
    )

    contextual = engine.report(
        created["investigation_id"],
        maximum_results=100,
        maximum_context_characters=5_000,
        include_context=True,
    )
    returned_text = sum(
        budgeted_evidence_text(item)
        for item in contextual["evidencias_documentales"]
    )
    assert returned_text <= COMPACT_REPORT_TEXT_LIMIT


@pytest.mark.asyncio
async def test_report_deduplicates_diagnostics_and_complete_mode_keeps_detail(tmp_path) -> None:
    class DiagnosticSource(FakeSourceAdapter):
        def get_report(self, source_investigation_id, **kwargs):
            del kwargs
            duplicate = {
                "fuente": "galiciana", "query": "persona", "ok": False,
                "error_type": "Timeout", "error": "agotado",
            }
            return {
                "cobertura": 1.0,
                "total_resultados": 1,
                "paginas_leidas": 1,
                "paginas_pendientes": 0,
                "paginas_fallidas": 0,
                "diagnosticos_fuente": [duplicate, dict(duplicate)],
                "evidencias_documentales": [
                    {
                        "titulo": "Padrón",
                        "contexto": "Persona ejerce oficio de comerciante.",
                        "texto_ocr": "Persona ejerce oficio de comerciante.",
                        "detalle_especial": 7,
                    }
                ],
                "paginas_no_leidas": [],
                "familia_documentada_o_candidata": [],
                "source_investigation_id": source_investigation_id,
            }

    engine, _ = make_engine(tmp_path, DiagnosticSource(complete_on_create=True))
    created = await engine.create_investigation(InvestigationTarget(name="Persona"))
    report = engine.report(
        created["investigation_id"], mode="completo", include_context=True
    )
    evidence = report["evidencias_documentales"][0]

    assert len(report["cobertura_por_fuente"][0]["diagnosticos"]) == 1
    assert evidence["detalle_especial"] == 7
    assert evidence["contexto"] == "Persona ejerce oficio de comerciante."
    assert "texto_ocr" not in evidence
    assert "profesión" in evidence["categorias"]
    assert evidence["hechos_extraidos"][0].keys() == {
        "tipo", "valor", "confianza", "texto_soporte"
    }


@pytest.mark.asyncio
async def test_complete_mode_budgets_unknown_large_text_and_keeps_traceability(
    tmp_path,
) -> None:
    class SpecialisedSource(FakeSourceAdapter):
        def get_report(self, source_investigation_id, **kwargs):
            del kwargs
            return {
                "cobertura": 1.0,
                "total_resultados": 1,
                "paginas_leidas": 1,
                "paginas_pendientes": 0,
                "paginas_fallidas": 0,
                "evidencias_documentales": [
                    {
                        "fecha": "1912-03-04",
                        "titulo": "Expediente especializado",
                        "pagina": "7",
                        "url_pagina": "https://example.invalid/page/7",
                        "url_ocr": "https://example.invalid/page/7/ocr",
                        "url_imagen": "https://example.invalid/page/7/image",
                        "detalle_pequeno": "compatible",
                        "campo_especial_grande": "Z" * 500_000,
                        "texto_ocr": "Persona Objetivo consta en el documento.",
                    }
                ],
                "paginas_no_leidas": [],
                "familia_documentada_o_candidata": [],
                "source_investigation_id": source_investigation_id,
            }

    engine, _ = make_engine(tmp_path, SpecialisedSource(complete_on_create=True))
    created = await engine.create_investigation(
        InvestigationTarget(name="Persona Objetivo")
    )
    report = engine.report(
        created["investigation_id"],
        mode="completo",
        maximum_context_characters=0,
    )
    evidence = report["evidencias_documentales"][0]

    assert budgeted_evidence_text(evidence) <= COMPLETE_REPORT_TEXT_LIMIT
    assert len(evidence["campo_especial_grande"]) < 500_000
    assert evidence["detalle_pequeno"] == "compatible"
    assert evidence["url_pagina"].endswith("/7")
    assert evidence["url_ocr"].endswith("/7/ocr")
    assert evidence["url_imagen"].endswith("/7/image")
    assert evidence["investigation_id_fuente"]
    assert report["paginacion"]["devueltos"] == 1
    assert report["paginacion"]["total_evidencias"] == 1
    assert report["texto_truncado"] is True
    assert report["limite_texto"] == COMPLETE_REPORT_TEXT_LIMIT


@pytest.mark.asyncio
async def test_extracted_facts_do_not_invent_unstated_family_relationships(tmp_path) -> None:
    engine, _ = make_engine(tmp_path, FakeSourceAdapter(complete_on_create=True))
    created = await engine.create_investigation(InvestigationTarget(name="Persona"))
    report = engine.report(created["investigation_id"])
    evidence = report["evidencias_documentales"][0]

    assert evidence["categorias"] == ["mención nominal"]
    assert evidence["hechos_extraidos"] == []
    assert all("parentesco" not in fact["tipo"] for fact in evidence["hechos_extraidos"])


@pytest.mark.asyncio
async def test_read_source_bounds_large_ocr_and_removes_text_duplicates() -> None:
    full_text = "A" * 60_000 + " NOMBRE OBJETIVO " + "B" * 60_000

    class FakeGalicianaEngine:
        timeout = 12
        transport = None
        store = SimpleNamespace(investigation=lambda investigation_id: None)

    class LargeConnector:
        async def read_page(self, url):
            return {
                "estado": "ok",
                "lectura_completa": True,
                "url": url,
                "ocr_url": "https://example.invalid/alto",
                "imagen_pagina": "https://example.invalid/image",
                "mets_url": "https://example.invalid/mets",
                "texto_ocr": full_text,
                "texto_visible": full_text,
                "mets": "M" * 20_000,
                "errores_recuperacion": ["diagnóstico compacto"],
            }

    adapter = GalicianaSourceAdapter(
        FakeGalicianaEngine(), connector=LargeConnector()
    )
    result = await adapter.read_source(
        "https://example.invalid/page",
        terms=["NOMBRE OBJETIVO"],
        maximum_characters=1_200,
    )

    returned_text = len(result["content"]) + sum(
        len(context) for context in result["contextos"]
    )
    assert returned_text <= 1_200
    assert "texto_ocr" not in result["detalle_fuente"]
    assert "texto_visible" not in result["detalle_fuente"]
    assert result["detalle_fuente"]["longitud_texto_original"] == len(full_text)
    assert len(result["detalle_fuente"]["mets"]) <= 600
    assert len(json.dumps(result, ensure_ascii=False)) < 10_000


@pytest.mark.asyncio
async def test_galiciana_adapter_delegates_without_reimplementing_source(tmp_path) -> None:
    class FakeGalicianaEngine:
        timeout = 12
        transport = None
        store = SimpleNamespace(investigation=lambda investigation_id: {"diagnostics_json": "[]"})

        def __init__(self) -> None:
            self.calls = []

        async def create_investigation(self, query, **kwargs):
            self.calls.append(("create", query.name, kwargs))
            return {"investigation_id": "gal-child", "estado": "pending"}

        async def process(self, investigation_id, **kwargs):
            self.calls.append(("process", investigation_id, kwargs))
            return {"completada": True}

        def report(self, investigation_id, **kwargs):
            self.calls.append(("report", investigation_id, kwargs))
            return {"evidencias_documentales": []}

    class FakeConnector:
        async def read_page(self, url):
            return {
                "estado": "ok",
                "url": url,
                "texto_ocr": "Andrés aparece en el OCR",
                "ocr_url": "https://example.invalid/alto",
                "imagen_pagina": "https://example.invalid/image",
            }

    galiciana = FakeGalicianaEngine()
    adapter = GalicianaSourceAdapter(galiciana, connector=FakeConnector())
    created = await adapter.create_investigation(
        InvestigationTarget(name="Andrés"), maximum_queries=4, maximum_results=10
    )
    processed = await adapter.process_next_batch(
        "gal-child", batch_size=2, time_budget_seconds=20
    )
    report = adapter.get_report(
        "gal-child", offset=0, maximum_results=10, include_pending=True
    )
    read = await adapter.read_source(
        "https://example.invalid/page", terms=["Andrés"], maximum_characters=100
    )

    assert created["source_investigation_id"] == "gal-child"
    assert processed["status"] == "complete"
    assert report == {"evidencias_documentales": [], "diagnosticos_fuente": []}
    assert read["contextos"] == ["Andrés aparece en el OCR"]
    assert read["documento"]["url_imagen"].endswith("/image")
    assert [call[0] for call in galiciana.calls] == ["create", "process", "report"]
