from __future__ import annotations

import re
import sqlite3
import time as system_time
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from rob.connectors.galiciana_ocr import (
    GalicianaOCRMention,
    OCRInterpretation,
    OCRSearchDiagnostic,
)
from rob.galiciana_investigations import (
    GalicianaInvestigationEngine,
    InvestigationStore,
    extract_alto_layout,
    extract_article_context,
    extract_family_relations,
)
from rob.models import GenealogyQuery


ALTO = '''<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
<Layout><Page WIDTH="2400" HEIGHT="3600"><PrintSpace>
<TextBlock HPOS="100" VPOS="100" WIDTH="900" HEIGHT="80">
  <TextLine HPOS="100" VPOS="100" WIDTH="900" HEIGHT="40"><String CONTENT="NECROLOGÍA"/></TextLine>
</TextBlock>
<TextBlock HPOS="100" VPOS="220" WIDTH="900" HEIGHT="500">
  <TextLine HPOS="100" VPOS="220" WIDTH="900" HEIGHT="40"><String CONTENT="Don"/><String CONTENT="Andrés"/><String CONTENT="Fernández"/><String CONTENT="Táboas"/></TextLine>
  <TextLine HPOS="100" VPOS="270" WIDTH="900" HEIGHT="40"><String CONTENT="y"/><String CONTENT="su"/><String CONTENT="esposa,"/><String CONTENT="doña"/><String CONTENT="Raquel"/><String CONTENT="Valenzuela"/><String CONTENT="Martínez."/></TextLine>
  <TextLine HPOS="100" VPOS="320" WIDTH="900" HEIGHT="40"><String CONTENT="Hijos:"/><String CONTENT="don"/><String CONTENT="Américo"/><String CONTENT="Fernández"/><String CONTENT="Valenzuela;"/></TextLine>
  <TextLine HPOS="100" VPOS="370" WIDTH="900" HEIGHT="40"><String CONTENT="don"/><String CONTENT="Celso"/><String CONTENT="Fernández"/><String CONTENT="Valenzuela."/></TextLine>
</TextBlock>
</PrintSpace></Page></Layout></alto>'''


def make_report(mention_count: int = 1) -> SimpleNamespace:
    mention = GalicianaOCRMention(
        record_id="1001",
        title="Noticiero de Vigo: 1911 agosto 25",
        parent_publication="Noticiero de Vigo",
        date="1911-08-25",
        page="Página 3",
        record_url="https://biblioteca.galiciana.gal/es/consulta/registro.do?id=1001",
        page_url="https://biblioteca.galiciana.gal/es/catalogo_imagenes/grupo.do?path=500&idImagen=900",
        digital_copy_url=None,
        pdf_url=None,
        path="500",
        image_id="900",
        snippets=["Andrés Fernández Táboas y Raquel Valenzuela Martínez"],
        matched_query='"andres fernandez taboas"',
        score=0.95,
        score_reasons=["nombre completo"],
        interpretations=[
            OCRInterpretation(
                category="familia",
                statement="referencia familiar",
                indicators=["su esposa"],
                confidence=0.9,
            )
        ],
    )
    return SimpleNamespace(
        status="ok",
        queries=['"andres fernandez taboas"'],
        diagnostics=[OCRSearchDiagnostic(query='"andres fernandez taboas"', ok=True)],
        mentions=[
            replace(
                mention,
                record_id=str(1001 + index),
                page=f"Página {3 + index}",
                page_url=f"https://biblioteca.galiciana.gal/es/catalogo_imagenes/grupo.do?path=500&idImagen={900 + index}",
                image_id=str(900 + index),
            )
            for index in range(mention_count)
        ],
        note="fixture",
    )


class FakeConnector:
    def __init__(self, *args, **kwargs):
        pass

    async def investigate(self, query, **kwargs):
        return make_report()


def test_alto_geometry_and_article_context() -> None:
    layout = extract_alto_layout(ALTO)
    assert len(layout) == 5
    context = extract_article_context(
        layout,
        "",
        ["Andrés Fernández Táboas", "Andres Fernandez Taboas"],
    )
    assert context["method"] == "alto-blocks-and-geometry"
    assert "NECROLOGÍA" in context["context"]
    assert "Raquel Valenzuela Martínez" in context["context"]


def test_family_extraction_is_explicit() -> None:
    relations = extract_family_relations(
        "Don Andrés Fernández Táboas y su esposa, doña Raquel Valenzuela Martínez. "
        "Hijos: don Américo Fernández Valenzuela; don Celso Fernández Valenzuela.",
        "Andrés Fernández Táboas",
    )
    names = {item["relative_name"] for item in relations}
    assert "Raquel Valenzuela Martínez" in names
    assert any("Américo Fernández Valenzuela" in name for name in names)
    assert not extract_family_relations(
        "Acudieron Andrés Fernández Táboas y Manuel Fernández Táboas.",
        "Andrés Fernández Táboas",
    )


def test_sqlite_is_the_default_storage_backend(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = InvestigationStore(str(tmp_path / "local.sqlite3"))
    assert store.storage_status() == {
        "backend": "sqlite",
        "persistent": False,
        "configured_by": "ROB_DB_PATH",
    }
    assert store._sql("SELECT * FROM mentions WHERE investigation_id=?") == (
        "SELECT * FROM mentions WHERE investigation_id=?"
    )


def test_postgresql_dialect_conversion_without_exposing_url() -> None:
    store = object.__new__(InvestigationStore)
    store.backend = "postgresql"
    store.database_url = "postgresql://secret-value"
    assert store._sql("SELECT * FROM mentions WHERE investigation_id=? LIMIT ?") == (
        "SELECT * FROM mentions WHERE investigation_id=%s LIMIT %s"
    )
    assert "secret-value" not in str(store.storage_status())


class PostgresTestConnection:
    """Small persistent adapter that executes the PostgreSQL store path on SQLite."""

    def __init__(self, path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")

    @staticmethod
    def _translate(statement: str) -> str:
        statement = statement.replace("%s", "?")
        statement = statement.replace("BIGSERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
        statement = statement.replace(" FOR UPDATE SKIP LOCKED", "")
        statement = re.sub(
            r"STRING_AGG\(([^,]+), ' \|\| '\)",
            r"GROUP_CONCAT(\1, ' || ')",
            statement,
        )
        return statement

    def execute(self, statement, params=()):
        return self.connection.execute(self._translate(statement), params)

    def executemany(self, statement, params):
        return self.connection.executemany(self._translate(statement), params)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None:
            self.connection.commit()
        else:
            self.connection.rollback()
        self.connection.close()


class PostgresTestStore(InvestigationStore):
    def __init__(self, adapter_path) -> None:
        self.adapter_path = adapter_path
        super().__init__(database_url="postgresql://adapter.invalid/test", ttl_days=0)

    def _connect(self):
        return PostgresTestConnection(self.adapter_path)


def test_postgresql_adapter_schema_crud_cache_relations_and_persistence(tmp_path) -> None:
    adapter_path = tmp_path / "postgres-adapter.sqlite3"
    first = PostgresTestStore(adapter_path)
    investigation_id = first.create(
        GenealogyQuery(name="Andrés Fernández Táboas", variants=[], places=["Vigo"]),
        make_report(),
    )
    mention_key = first.pending(investigation_id, 1)[0]["mention_key"]
    first.cache_put(
        path="500",
        page_number=3,
        ocr_url="https://example.invalid/alto",
        image_url=None,
        full_text="texto persistente",
        layout=[{"text": "texto persistente"}],
    )
    relation = {
        "relation_type": "cónyuge",
        "relative_name": "Raquel Valenzuela Martínez",
        "evidence": "su esposa, doña Raquel Valenzuela Martínez",
        "confidence": 0.9,
    }
    first.add_relations(
        investigation_id, mention_key, "Andrés", "https://example.invalid/3", [relation]
    )
    first.add_relations(
        investigation_id, mention_key, "Andrés", "https://example.invalid/3", [relation]
    )

    second = PostgresTestStore(adapter_path)
    assert second.investigation(investigation_id)["id"] == investigation_id
    assert second.query(investigation_id).name == "Andrés Fernández Táboas"
    assert second.cache_get("500", 3)["full_text"] == "texto persistente"
    relations = second.relations(investigation_id)
    assert len(relations) == 1
    assert relations[0]["evidence_count"] == 1
    assert relations[0]["relative_name"] == "Raquel Valenzuela Martínez"

    with sqlite3.connect(adapter_path) as connection:
        objects = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
            )
        }
    assert {"investigations", "mentions", "page_cache", "relations"} <= objects
    assert {"idx_mentions_pending", "idx_mentions_path", "idx_relations_investigation"} <= objects


def test_ttl_is_disabled_by_default_and_can_be_enabled(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ROB_INVESTIGATION_TTL_DAYS", raising=False)
    path = tmp_path / "ttl.sqlite3"
    store = InvestigationStore(str(path))
    investigation_id = store.create(
        GenealogyQuery(name="Persona Persistente", variants=[], places=[]), make_report()
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE investigations SET updated_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (investigation_id,),
        )
    assert InvestigationStore(str(path)).investigation(investigation_id) is not None
    monkeypatch.setenv("ROB_INVESTIGATION_TTL_DAYS", "1")
    assert InvestigationStore(str(path)).investigation(investigation_id) is None


@pytest.mark.asyncio
async def test_time_budget_does_not_abandon_a_claimed_batch(tmp_path, monkeypatch) -> None:
    class MultipleMentionsConnector(FakeConnector):
        async def investigate(self, query, **kwargs):
            return make_report(3)

    monkeypatch.setattr(
        "rob.galiciana_investigations.GalicianaOCRConnector", MultipleMentionsConnector
    )
    monotonic_values = iter([0.0, 20.0])
    monkeypatch.setattr(
        "rob.galiciana_investigations.time",
        SimpleNamespace(monotonic=lambda: next(monotonic_values), time=system_time.time),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(("/es/inicio/inicio.do", "/es/consulta/busqueda.do")):
            return httpx.Response(200, text="<html></html>")
        if request.url.path.endswith("/catalogo_imagenes/descargarAlto.do"):
            return httpx.Response(200, text=ALTO)
        raise AssertionError(str(request.url))

    engine = GalicianaInvestigationEngine(
        store=InvestigationStore(str(tmp_path / "budget.sqlite3")),
        transport=httpx.MockTransport(handler),
    )
    created = await engine.create_investigation(
        GenealogyQuery(name="Andrés Fernández Táboas", variants=[], places=[])
    )
    result = await engine.process(created["investigation_id"], batch_size=3, time_budget_seconds=10)
    assert result["completada"] is False
    assert result["paginas_leidas"] == 1
    assert result["paginas_pendientes"] == 2
    assert result["paginas_leyendo"] == 0


@pytest.mark.asyncio
async def test_reading_rows_never_report_complete(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("rob.galiciana_investigations.GalicianaOCRConnector", FakeConnector)
    store = InvestigationStore(str(tmp_path / "reading.sqlite3"))
    engine = GalicianaInvestigationEngine(
        store=store,
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    )
    created = await engine.create_investigation(
        GenealogyQuery(name="Andrés Fernández Táboas", variants=[], places=[])
    )
    assert store.claim_pending(created["investigation_id"]) is not None
    result = await engine.process(created["investigation_id"])
    assert result["completada"] is False
    assert result["paginas_leyendo"] == 1


@pytest.mark.asyncio
async def test_resumable_batch_reads_direct_alto_and_uses_cache(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("rob.galiciana_investigations.GalicianaOCRConnector", FakeConnector)
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path.endswith("/es/inicio/inicio.do") or request.url.path.endswith("/es/consulta/busqueda.do"):
            return httpx.Response(200, text="<html></html>")
        if request.url.path.endswith("/catalogo_imagenes/descargarAlto.do"):
            return httpx.Response(200, text=ALTO, headers={"content-type": "application/xml"})
        raise AssertionError(f"Unexpected request {request.url}")

    store = InvestigationStore(str(tmp_path / "rob.sqlite3"))
    engine = GalicianaInvestigationEngine(
        store=store,
        transport=httpx.MockTransport(handler),
    )
    query = GenealogyQuery(
        name="Andrés Fernández Táboas",
        variants=["Andres Fernandez Taboas"],
        places=["Vigo"],
    )
    created = await engine.create_investigation(query)
    result = await engine.process(created["investigation_id"], batch_size=5)
    assert result["completada"] is True
    assert result["paginas_leidas"] == 1
    report = engine.report(created["investigation_id"])
    assert report["evidencias_documentales"][0]["fuente_texto"] == "ALTO XML completo"
    assert "Raquel Valenzuela Martínez" in report["evidencias_documentales"][0]["contexto"]
    assert report["familia_documentada_o_candidata"]

    # A second dossier for the same physical page must use the persisted cache.
    created2 = await engine.create_investigation(query)
    result2 = await engine.process(created2["investigation_id"], batch_size=5)
    assert result2["cache_hits"] == 1
    alto_requests = [url for url in requests if "descargarAlto.do" in url]
    assert len(alto_requests) == 1

@pytest.mark.asyncio
async def test_family_search_requires_explicit_relation(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("rob.galiciana_investigations.GalicianaOCRConnector", FakeConnector)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/es/inicio/inicio.do") or request.url.path.endswith("/es/consulta/busqueda.do"):
            return httpx.Response(200, text="<html></html>")
        if request.url.path.endswith("/catalogo_imagenes/descargarAlto.do"):
            return httpx.Response(200, text=ALTO)
        raise AssertionError(str(request.url))

    engine = GalicianaInvestigationEngine(
        store=InvestigationStore(str(tmp_path / "family.sqlite3")),
        transport=httpx.MockTransport(handler),
    )
    parent = await engine.create_investigation(
        GenealogyQuery(name="Andrés Fernández Táboas", variants=[], places=["Vigo"])
    )
    await engine.process(parent["investigation_id"])
    child = await engine.create_family_investigation(
        parent["investigation_id"], "Raquel Valenzuela Martínez"
    )
    assert child["relacion_previamente_documentada"] is True

    with pytest.raises(ValueError):
        await engine.create_family_investigation(
            parent["investigation_id"], "Persona Inventada Apellidos"
        )
