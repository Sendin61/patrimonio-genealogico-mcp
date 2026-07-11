from __future__ import annotations

import httpx
import pytest

from rob.connectors.galiciana_ocr import (
    GalicianaOCRConnector,
    build_ocr_queries,
    extract_antibot_challenge,
    parse_results_html,
    unpack_dean_edwards_packer,
)
from rob.models import GenealogyQuery


FIXTURE_HTML = """
<!DOCTYPE html>
<html lang="es"><body>
<a href="../consulta/resultados_ocr.do?general_ocr=on&id=1430&tipoResultados=PAG">Páginas</a>
<div class="nav_paginas"><span class="nav_descrip">1 al 2 de 2</span></div>
<ol class="nav_registros" id="nav_registros">
<li class="nav_registro">
<section class="registro_bib">
<!-- Id: 10000333989 -->
<dl id="dl_10000333989">
<dt><span class="titulo"><a href="../consulta/registro.do?id=10000333989">Boletín Oficial de la Provincia de Lugo: Número 135 - 1879 novembro 11</a></span>
<span class="publicacion_madre">En: Boletín Oficial de la Provincia de Lugo</span></dt>
<dd>
<p class="gruposimagenes"><a href="../catalogo_imagenes/grupo.do?path=1356353&texto_busqueda=%22manuel+perez+eiriz%22"><span>Copia dixital</span></a></p>
<ul><li class="unidad_textual">
<a id="img13274184" href="../catalogo_imagenes/grupo.do?path=1356353&idImagen=13274184&texto_busqueda=%22manuel+perez+eiriz%22">Página 2</a>
<ul class="texto_ocurrencias"><li>Don <strong>Manuel Perez Eiriz</strong>, Capitán graduado, Teniente Ayudante del Batallón.</li></ul>
</li></ul>
</dd></dl></section></li>
<li class="nav_registro">
<section class="registro_bib"><dl id="dl_10000385381">
<dt><span class="titulo"><a href="../consulta/registro.do?id=10000385381">El Miño : diario liberal: Año VII Número 1631 - 1904 mayo 3</a></span>
<span class="publicacion_madre">En: El Miño : diario liberal</span></dt>
<dd><p class="gruposimagenes"><a href="../catalogo_imagenes/grupo.do?path=1430071"><span>Copia dixital</span></a></p>
<ul><li class="unidad_textual"><a id="img13790309" href="../catalogo_imagenes/grupo.do?path=1430071&idImagen=13790309&texto_busqueda=%22manuel+perez+eiriz%22">Página 1</a>
<ul class="texto_ocurrencias"><li>2.250 pesetas a los hijos del primer teniente D. <strong>Manuel Pérez Eiriz</strong>.</li></ul></li></ul>
<div><a href="../catalogo_imagenes/grupo.do?path=1434145"><span>Versión PDF</span></a></div>
</dd></dl></section></li>
</ol>
</body></html>
"""


def test_build_ocr_queries_prioritises_real_working_form() -> None:
    query = GenealogyQuery(
        name="Manuel Pérez Eiriz",
        variants=["Pérez Eiriz, Manuel"],
    )
    values = build_ocr_queries(query, maximum=6)

    assert values[0] == '"manuel perez eiriz"'
    assert '"manuel pérez eiriz"' in values
    assert any("perez eiriz" in value for value in values)
    assert len(values) <= 6


def test_parse_realistic_results_html() -> None:
    query = GenealogyQuery(
        name="Manuel Pérez Eiriz",
        places=["Viana", "Lugo"],
        year_from=1870,
        year_to=1910,
    )
    parsed = parse_results_html(
        FIXTURE_HTML,
        matched_query='"manuel perez eiriz"',
        genealogy_query=query,
    )

    assert parsed.search_id == "1430"
    assert parsed.total_reported == 2
    assert len(parsed.mentions) == 2

    first = parsed.mentions[0]
    assert first.record_id == "10000333989"
    assert first.date == "1879-11-11"
    assert first.page == "Página 2"
    assert first.path == "1356353"
    assert first.image_id == "13274184"
    assert first.score >= 0.8
    assert "militar" in {item.category for item in first.interpretations}

    second = parsed.mentions[1]
    assert second.date == "1904-05-03"
    assert second.pdf_url is not None
    assert "familia" in {item.category for item in second.interpretations}
    assert "aportación económica" in {
        item.category for item in second.interpretations
    }


def test_unpack_simple_dean_edwards_payload() -> None:
    packed = """eval(function(p,a,c,k,e,d){return p}('0 1=\"2\";',3,3,'var|x|ok'.split('|'),0,{}))"""
    assert unpack_dean_edwards_packer(packed) == 'var x="ok";'


def test_extract_antibot_from_decoded_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    decoded = """
    var cookieName="cookiesession8341";
    var cookieEncoded="QUJDMTIz";
    var url="/es/consulta/resultados_ocr.do";
    url+="?"+cookieName+"="+decode(cookieEncoded);
    var send_data="fwb_dat="+"UE9TVA==";
    """
    monkeypatch.setattr(
        "rob.connectors.galiciana_ocr.unpack_dean_edwards_packer",
        lambda script: decoded,
    )
    html = "<html><script>eval(function(p,a,c,k,e,d){})</script></html>"
    endpoint, name, value, payload = extract_antibot_challenge(html)

    assert endpoint.endswith("/es/consulta/resultados_ocr.do")
    assert name == "cookiesession8341"
    assert value == "ABC123"
    assert payload == "UE9TVA=="


@pytest.mark.asyncio
async def test_connector_searches_and_returns_interpreted_report() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/es/consulta/busqueda.do"):
            return httpx.Response(200, text="<html></html>")
        assert request.url.path.endswith("/es/consulta/resultados_ocr.do")
        return httpx.Response(200, text=FIXTURE_HTML)

    connector = GalicianaOCRConnector(
        transport=httpx.MockTransport(handler),
    )
    report = await connector.investigate(
        GenealogyQuery(
            name="Manuel Pérez Eiriz",
            places=["Lugo"],
            year_from=1870,
            year_to=1910,
        ),
        maximum_queries=1,
        maximum_results=20,
    )

    assert report.status == "ok"
    assert report.total_unique == 2
    assert report.diagnostics[0].ok is True
    assert report.findings
    assert report.chronology[0]["date"] == "1879-11-11"


def test_antibot_payload_fallback_finds_encoded_original_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import base64

    request_text = (
        "POST /es/consulta/resultados_ocr.do HTTP/1.1\r\n"
        "Host: biblioteca.galiciana.gal\r\n\r\n"
        'general_ocr=on&busq_general=%22manuel+perez+eiriz%22'
    )
    payload = base64.b64encode(request_text.encode()).decode()
    decoded = f"""
    var cookieName="cookiesession8341";
    var cookieEncoded="QUJDMTIz";
    var url="/es/consulta/resultados_ocr.do";
    url+="?"+cookieName+"="+decode(cookieEncoded);
    var strange="{payload}";
    var send_data=makePayload(strange);
    """
    monkeypatch.setattr(
        "rob.connectors.galiciana_ocr.unpack_dean_edwards_packer",
        lambda script: decoded,
    )
    html = "<html><script>eval(function(p,a,c,k,e,d){})</script></html>"

    _, _, _, extracted = extract_antibot_challenge(html)
    assert extracted == payload


@pytest.mark.asyncio
async def test_connector_deduplicates_pages_and_enforces_date_range() -> None:
    out_of_range = FIXTURE_HTML.replace(
        "El Miño : diario liberal: Año VII Número 1631 - 1904 mayo 3",
        "El Miño : diario liberal: Año XX Número 5000 - 1935 mayo 3",
    )

    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path.endswith("/es/consulta/busqueda.do"):
            return httpx.Response(200, text="<html></html>")
        calls += 1
        # First exact query returns both pages, second exact variant returns
        # the same pages. The 1935 page must be filtered and the 1879 page
        # must not be duplicated.
        return httpx.Response(200, text=out_of_range)

    connector = GalicianaOCRConnector(transport=httpx.MockTransport(handler))
    report = await connector.investigate(
        GenealogyQuery(
            name="Manuel Pérez Eiriz",
            variants=["Pérez Eiriz, Manuel"],
            year_from=1870,
            year_to=1910,
        ),
        maximum_queries=4,
        maximum_results=20,
    )

    assert report.status == "ok"
    assert report.total_unique == 1
    assert report.mentions[0].date == "1879-11-11"
    assert all(
        mention.date is None or int(mention.date[:4]) <= 1910
        for mention in report.mentions
    )
    assert sum(item.discarded_out_of_range for item in report.diagnostics) >= 1
    # Broad queries are skipped once exact phrases yield enough hits.
    assert all("?" not in item.query for item in report.diagnostics)


@pytest.mark.asyncio
async def test_connector_retries_remote_disconnect() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path.endswith("/es/consulta/busqueda.do"):
            return httpx.Response(200, text="<html></html>")
        attempts += 1
        if attempts == 1:
            raise httpx.RemoteProtocolError(
                "Server disconnected without sending a response."
            )
        return httpx.Response(200, text=FIXTURE_HTML)

    connector = GalicianaOCRConnector(transport=httpx.MockTransport(handler))
    report = await connector.investigate(
        GenealogyQuery(
            name="Manuel Pérez Eiriz",
            year_from=1870,
            year_to=1910,
        ),
        maximum_queries=1,
        maximum_results=20,
    )

    assert report.status == "ok"
    assert report.diagnostics[0].attempts == 2
    assert report.total_unique == 2
