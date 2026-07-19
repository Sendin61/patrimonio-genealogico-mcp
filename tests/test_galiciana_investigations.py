from __future__ import annotations

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


def make_report() -> SimpleNamespace:
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
        mentions=[mention],
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
