from __future__ import annotations

import asyncio

import json
from typing import Any

import httpx
import pytest

from rob.connectors.exa import ExaAPIError, ExaClient
from rob.investigations import (
    ExaInvestigationStore,
    ExaSourceAdapter,
    InvestigationTarget,
    UniversalInvestigationEngine,
    UniversalInvestigationStore,
)
from rob.investigations.models import SourceCapabilities


def client(handler, key: str = "secret-exa-key") -> ExaClient:
    return ExaClient(key, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_exa_client_uses_headers_endpoints_bounds_and_accepts_extra_fields() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        if request.url.path == "/search":
            assert payload["type"] == "auto"
            return httpx.Response(
                200,
                json={
                    "requestId": "request-1",
                    "costDollars": 0.01,
                    "unknown": {"future": True},
                    "results": [{"url": "https://example.org/a", "title": "A", "extra": 1}],
                },
            )
        assert request.url.path == "/contents"
        assert payload["ids"] == ["https://example.org/a"]
        assert payload["highlights"] == {
            "query": "persona",
            "maxCharacters": 8000,
        }
        assert payload["text"] == {"maxCharacters": 8000}
        assert "numSentences" not in payload["highlights"]
        assert "highlightsPerUrl" not in payload["highlights"]
        return httpx.Response(
            200,
            json={
                "results": [{"url": "https://example.org/a", "highlights": ["H" * 3000], "text": "T" * 9000}],
                "statuses": [{
                    "id": "https://example.org/missing",
                    "status": "error",
                    "error": {
                        "tag": "NOT_FOUND",
                        "httpStatusCode": 404,
                        "message": "secret-exa-key and a long upstream body",
                    },
                    "unknown": "secret-exa-key",
                }],
                "extra": True,
            },
        )

    exa = client(handler)
    search = await exa.search("persona", num_results=5)
    contents = await exa.contents(
        ["https://example.org/a"], highlight_query="persona", max_characters=9000
    )

    assert all(request.headers["x-api-key"] == "secret-exa-key" for request in requests)
    assert search["diagnostics"] == {"requestId": "request-1", "costDollars": 0.01}
    assert len(contents["results"][0]["highlights"][0]) == 1600
    assert len(contents["results"][0]["text"]) == 8000
    assert contents["statuses"] == [{
        "id": "https://example.org/missing",
        "status": "error",
        "error": {"tag": "NOT_FOUND", "httpStatusCode": 404},
    }]
    assert "secret-exa-key" not in repr(search) + repr(contents)


@pytest.mark.asyncio
async def test_exa_client_rejects_bad_json_without_leaking_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    with pytest.raises(ExaAPIError) as error:
        await client(handler).search("persona", num_results=2)
    assert "secret-exa-key" not in str(error.value)


@pytest.mark.asyncio
async def test_unavailable_exa_never_attempts_network(tmp_path) -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        raise AssertionError("network must not be attempted")

    adapter = ExaSourceAdapter(
        ExaInvestigationStore(str(tmp_path / "exa.sqlite3")),
        api_key="",
        client=client(handler, key=""),
    )
    engine = UniversalInvestigationEngine(
        UniversalInvestigationStore(str(tmp_path / "universal.sqlite3")), [adapter]
    )

    assert adapter.available is False
    with pytest.raises(ValueError, match="no disponible"):
        await engine.create_investigation(
            InvestigationTarget(name="Persona"), requested_sources=["exa"]
        )
    assert called is False


@pytest.mark.asyncio
async def test_creation_queries_deduplicate_urls_order_and_partial_failure(tmp_path) -> None:
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        queries.append(payload["query"])
        if len(queries) == 2:
            return httpx.Response(503, json={"error": "temporary"})
        return httpx.Response(
            200,
            json={"results": [
                {"url": "https://example.org/a?utm_source=x", "title": "A"},
                {"url": "https://example.org/a", "title": "duplicate"},
                {"url": f"https://example.org/{len(queries)}", "title": "ordered"},
            ]},
        )

    store = ExaInvestigationStore(str(tmp_path / "exa.sqlite3"))
    adapter = ExaSourceAdapter(store, api_key="key", client=client(handler, "key"))
    result = await adapter.create_investigation(
        InvestigationTarget(
            name="Ana Pérez", variants=["Ana Perez", "Ana Pérez"], places=["Lugo"],
            year_from=1880, year_to=1940, spouse="Luis", profession="maestra",
        ),
        maximum_queries=5,
        maximum_results=4,
    )

    assert result["status"] == "processing"
    assert result["detail"]["consultas_efectivas"] == 5
    assert result["detail"]["total_candidatos_unicos"] <= 4
    assert len(queries) <= 5
    assert len(queries) == len(set(query.casefold() for query in queries))
    assert all("Ana" in query for query in queries)
    assert any("Ana Perez" in query for query in queries)
    assert any("Lugo" in query for query in queries)
    assert any(not diagnostic["ok"] for diagnostic in result["diagnostics"])
    rows = store.rows(result["source_investigation_id"], 0, 20)
    assert [row["result_order"] for row in rows] == list(range(len(rows)))


@pytest.mark.asyncio
async def test_zero_results_and_total_search_failure_have_terminal_states(tmp_path) -> None:
    empty = ExaSourceAdapter(
        ExaInvestigationStore(str(tmp_path / "empty.sqlite3")), api_key="key",
        client=client(lambda request: httpx.Response(200, json={"results": []}), "key"),
    )
    empty_result = await empty.create_investigation(
        InvestigationTarget(name="Persona"), maximum_queries=1, maximum_results=3
    )
    assert empty_result["status"] == "complete"

    failed = ExaSourceAdapter(
        ExaInvestigationStore(str(tmp_path / "failed.sqlite3")), api_key="key",
        client=client(lambda request: httpx.Response(400, json={"error": "bad"}), "key"),
    )
    failed_result = await failed.create_investigation(
        InvestigationTarget(name="Persona"), maximum_queries=1, maximum_results=3
    )
    assert failed_result["status"] == "failed"


@pytest.mark.asyncio
async def test_persistence_batch_mapping_missing_url_and_local_report(tmp_path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/search":
            return httpx.Response(200, json={"results": [
                {"url": "https://one.example/a", "title": "One"},
                {"url": "https://two.example/b", "title": "Two"},
                {"url": "https://three.example/c", "title": "Three"},
            ]})
        payload = json.loads(request.content)
        assert len(payload["ids"]) == 2
        return httpx.Response(200, json={"results": [
            {"url": payload["ids"][1], "title": "Two read", "author": "Autor",
             "publishedDate": "1910", "highlights": ["Persona fue alcalde"], "score": 0.7}
        ]})

    path = str(tmp_path / "exa.sqlite3")
    first = ExaSourceAdapter(ExaInvestigationStore(path), api_key="key", client=client(handler, "key"))
    created = await first.create_investigation(
        InvestigationTarget(name="Persona"), maximum_queries=1, maximum_results=3
    )
    source_id = created["source_investigation_id"]

    rebuilt = ExaSourceAdapter(ExaInvestigationStore(path), api_key="key", client=client(handler, "key"))
    result = await rebuilt.process_next_batch(source_id, batch_size=2, time_budget_seconds=10)
    assert result["procesados_en_esta_llamada"] == 2
    assert result["resultados_leidos"] == 1
    assert result["resultados_reintentables"] == 1
    calls_before_report = len(calls)
    report = rebuilt.get_report(source_id, offset=0, maximum_results=2, include_pending=True)
    assert len(calls) == calls_before_report
    assert report["evidencias_documentales"][0]["url_pagina"] == "https://two.example/b"
    assert report["evidencias_documentales"][0]["autor"] == "Autor"
    assert report["familia_documentada_o_candidata"] == []
    persisted = rebuilt.store.rows(source_id, 0, 3)
    read_row = next(row for row in persisted if row["status"] == "read")
    assert sum(map(len, read_row["highlights"])) + len(read_row["recovered_text"]) <= 8000
    first_page = rebuilt.get_report(source_id, offset=0, maximum_results=2, include_pending=True)
    second_page = rebuilt.get_report(source_id, offset=2, maximum_results=2, include_pending=True)
    urls = [item.get("url_pagina") for page in (first_page, second_page)
            for bucket in (page["evidencias_documentales"], page["paginas_no_leidas"])
            for item in bucket]
    assert len(urls) == len(set(urls)) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code,expected", [(429, "retryable"), (503, "retryable"), (400, "failed")])
async def test_processing_classifies_http_errors(tmp_path, status_code, expected) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(200, json={"results": [{"url": "https://example.org/a"}]})
        return httpx.Response(status_code, json={"error": "failure"})

    adapter = ExaSourceAdapter(
        ExaInvestigationStore(str(tmp_path / f"{status_code}.sqlite3")),
        api_key="key", client=client(handler, "key"),
    )
    created = await adapter.create_investigation(
        InvestigationTarget(name="Persona"), maximum_queries=1, maximum_results=1
    )
    await adapter.process_next_batch(created["source_investigation_id"], batch_size=1, time_budget_seconds=10)
    assert adapter.store.rows(created["source_investigation_id"], 0, 1)[0]["status"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code,expected", [(404, "failed"), (403, "failed"), (504, "retryable")]
)
async def test_processing_classifies_individual_content_statuses(
    tmp_path, status_code, expected
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(
                200, json={"results": [{"url": "https://example.org/a"}]}
            )
        return httpx.Response(200, json={
            "results": [],
            "statuses": [{
                "id": "https://example.org/a",
                "status": "error",
                "error": {"tag": "UPSTREAM", "httpStatusCode": status_code},
            }],
        })

    adapter = ExaSourceAdapter(
        ExaInvestigationStore(str(tmp_path / f"individual-{status_code}.sqlite3")),
        api_key="key",
        client=client(handler, "key"),
    )
    created = await adapter.create_investigation(
        InvestigationTarget(name="Persona"), maximum_queries=1, maximum_results=1
    )
    await adapter.process_next_batch(
        created["source_investigation_id"], batch_size=1, time_budget_seconds=10
    )
    row = adapter.store.rows(created["source_investigation_id"], 0, 1)[0]
    assert row["status"] == expected
    assert "UPSTREAM" in row["error"] and str(status_code) in row["error"]


@pytest.mark.asyncio
async def test_individual_retryable_status_fails_after_three_attempts(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(
                200, json={"results": [{"url": "https://example.org/a"}]}
            )
        return httpx.Response(200, json={
            "results": [],
            "statuses": [{
                "url": "https://example.org/a",
                "error": {"tag": "TIMEOUT", "httpStatusCode": 504},
            }],
        })

    adapter = ExaSourceAdapter(
        ExaInvestigationStore(str(tmp_path / "individual-retry.sqlite3")),
        api_key="key",
        client=client(handler, "key"),
    )
    created = await adapter.create_investigation(
        InvestigationTarget(name="Persona"), maximum_queries=1, maximum_results=1
    )
    for _ in range(3):
        await adapter.process_next_batch(
            created["source_investigation_id"], batch_size=1, time_budget_seconds=10
        )
    assert adapter.store.rows(created["source_investigation_id"], 0, 1)[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_read_source_raises_safe_individual_status(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "results": [],
            "statuses": [{
                "id": "https://example.org/direct",
                "status": "error",
                "error": {
                    "tag": "FORBIDDEN",
                    "httpStatusCode": 403,
                    "message": "secret-exa-key must not escape",
                },
            }],
        })

    adapter = ExaSourceAdapter(
        ExaInvestigationStore(str(tmp_path / "direct-status.sqlite3")),
        api_key="secret-exa-key",
        client=client(handler),
    )
    with pytest.raises(ExaAPIError) as caught:
        await adapter.read_source(
            "https://example.org/direct", terms=["Persona"], maximum_characters=100
        )
    assert caught.value.status_code == 403
    assert "FORBIDDEN" in str(caught.value)
    assert "secret-exa-key" not in str(caught.value)


@pytest.mark.asyncio
async def test_three_attempts_fail_and_direct_read_is_safe_bounded_and_not_duplicated(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(200, json={"results": [{"url": "https://example.org/a"}]})
        payload = json.loads(request.content)
        if payload["ids"] == ["https://example.org/direct"]:
            return httpx.Response(200, json={"results": [{
                "url": payload["ids"][0], "title": "Title", "author": "Author",
                "publishedDate": "1900", "image": "https://example.org/i.jpg",
                "highlights": ["H" * 80, "J" * 80], "text": "fallback" * 1000,
            }]})
        return httpx.Response(429, json={"error": "rate"})

    adapter = ExaSourceAdapter(
        ExaInvestigationStore(str(tmp_path / "retry.sqlite3")), api_key="key",
        client=client(handler, "key"),
    )
    created = await adapter.create_investigation(
        InvestigationTarget(name="Persona"), maximum_queries=1, maximum_results=1
    )
    for _ in range(3):
        await adapter.process_next_batch(created["source_investigation_id"], batch_size=1, time_budget_seconds=10)
    assert adapter.store.rows(created["source_investigation_id"], 0, 1)[0]["status"] == "failed"

    for unsafe in ("http://localhost/a", "http://127.0.0.1/a", "http://host.local/a", "http://user:pass@example.org"):
        with pytest.raises(ValueError):
            await adapter.read_source(unsafe, terms=["Persona"], maximum_characters=100)
    read = await adapter.read_source(
        "https://example.org/direct", terms=["Persona"], maximum_characters=100
    )
    assert sum(map(len, read["contextos"])) + len(read["content"]) <= 100
    assert read["content"] == ""
    assert "highlights" not in read["detalle_fuente"] and "text" not in read["detalle_fuente"]
    assert read["documento"]["autor"] == "Author"


@pytest.mark.asyncio
async def test_processing_uses_one_global_deadline_and_releases_claim(tmp_path) -> None:
    class SlowClient:
        async def search(self, query, *, num_results):
            return {"results": [{"url": "https://example.org/a"}], "diagnostics": {}}

        async def contents(self, urls, **kwargs):
            await asyncio.sleep(0.2)
            return {"results": [], "diagnostics": {}}

    adapter = ExaSourceAdapter(
        ExaInvestigationStore(str(tmp_path / "deadline.sqlite3")),
        api_key="key",
        client=SlowClient(),
    )
    created = await adapter.create_investigation(
        InvestigationTarget(name="Persona"), maximum_queries=1, maximum_results=1
    )
    await adapter.process_next_batch(
        created["source_investigation_id"], batch_size=1, time_budget_seconds=0.01
    )
    row = adapter.store.rows(created["source_investigation_id"], 0, 1)[0]
    assert row["status"] == "retryable"
    assert row["status"] != "reading"


class CoordinatedAdapter:
    capabilities = SourceCapabilities()
    available = True

    def __init__(self, name: str, fail: bool = False):
        self.source_name = name
        self.fail = fail

    async def create_investigation(self, target, **kwargs):
        return {"source_investigation_id": f"{self.source_name}-child", "status": "processing"}

    async def process_next_batch(self, source_investigation_id, **kwargs):
        if self.fail:
            raise RuntimeError("failed")
        return {"status": "complete", "complete": True}

    def get_report(self, source_investigation_id, **kwargs):
        return {"cobertura": 1.0, "total_resultados": 1, "paginas_leidas": 1,
                "paginas_pendientes": 0, "paginas_fallidas": 0,
                "evidencias_documentales": [{"titulo": self.source_name, "url_pagina": f"https://{self.source_name}.example"}],
                "paginas_no_leidas": [], "familia_documentada_o_candidata": [],
                "paginacion": {"devueltos": 1, "siguiente_desde": None}}

    async def read_source(self, source_url, **kwargs):
        return {}


@pytest.mark.asyncio
async def test_universal_coordinates_galiciana_and_exa(tmp_path) -> None:
    engine = UniversalInvestigationEngine(
        UniversalInvestigationStore(str(tmp_path / "universal.sqlite3")),
        [CoordinatedAdapter("galiciana"), CoordinatedAdapter("exa")],
    )
    created = await engine.create_investigation(
        InvestigationTarget(name="Persona"), requested_sources=["galiciana", "exa"]
    )
    assert [item["investigation_id_fuente"] for item in created["fuentes"]] == ["galiciana-child", "exa-child"]
    processed = await engine.process(created["investigation_id"])
    assert processed["estado"] == "complete"
    report = engine.report(created["investigation_id"], maximum_results=2)
    assert [item["titulo"] for item in report["evidencias_documentales"]] == ["galiciana", "exa"]
    assert [item["fuente"] for item in report["cobertura_por_fuente"]] == ["galiciana", "exa"]

    partial = UniversalInvestigationEngine(
        UniversalInvestigationStore(str(tmp_path / "partial.sqlite3")),
        [CoordinatedAdapter("galiciana"), CoordinatedAdapter("exa", fail=True)],
    )
    created_partial = await partial.create_investigation(
        InvestigationTarget(name="Persona"), requested_sources=["galiciana", "exa"]
    )
    assert (await partial.process(created_partial["investigation_id"]))["estado"] == "partial"
