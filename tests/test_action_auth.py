from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

import server


TEST_KEY = "test-only action key with spaces inside"


def client(monkeypatch, configured: bool = True) -> TestClient:
    if configured:
        monkeypatch.setenv("ROB_ACTION_KEY", TEST_KEY)
    else:
        monkeypatch.delenv("ROB_ACTION_KEY", raising=False)
    monkeypatch.setattr(
        server,
        "get_default_engine",
        lambda: SimpleNamespace(
            store=SimpleNamespace(
                storage_status=lambda: {
                    "backend": "sqlite",
                    "persistent": False,
                    "configured_by": "ROB_DB_PATH",
                }
            )
        ),
    )
    return TestClient(server.mcp.streamable_http_app())


def test_public_routes_do_not_require_action_key(monkeypatch) -> None:
    http = client(monkeypatch, configured=False)
    assert http.get("/health").status_code == 200
    assert http.get("/privacy").status_code == 200
    assert http.get("/openapi.json").status_code == 200
    health = http.get("/health").json()
    assert health["seguridad"] == {
        "autenticacion_configurada": False,
        "rutas_operativas_protegidas": True,
    }
    assert health["motor_universal"] is True
    assert health["motor_multifuente"] is True
    assert health["exa_configurada"] is False
    assert health["fuentes_universales_disponibles"] == ["galiciana"]
    assert health["version"] == http.get("/openapi.json").json()["info"]["version"] == "1.0.0"


def test_api_returns_503_when_server_key_is_not_configured(monkeypatch) -> None:
    response = client(monkeypatch, configured=False).post(
        "/api/galiciana/investigaciones/informe", json={}
    )
    assert response.status_code == 503
    assert response.json() == {"error": "Servicio no disponible."}


def test_api_rejects_missing_and_incorrect_keys_identically(monkeypatch) -> None:
    http = client(monkeypatch)
    missing = http.post("/api/galiciana/investigaciones/informe", json={})
    incorrect = http.post(
        "/api/galiciana/investigaciones/informe",
        json={},
        headers={"X-ROB-Key": "incorrect"},
    )
    assert missing.status_code == incorrect.status_code == 401
    assert missing.json() == incorrect.json() == {"error": "No autorizado."}


@pytest.mark.parametrize(
    "path",
    [
        "/api/investigacion/crear",
        "/api/investigacion/procesar",
        "/api/investigacion/informe",
        "/api/investigacion/leer-fuente",
    ],
)
def test_universal_routes_require_action_key(monkeypatch, path: str) -> None:
    response = client(monkeypatch).post(path, json={})
    assert response.status_code == 401
    assert response.json() == {"error": "No autorizado."}


def test_correct_key_passes_through_central_authentication(monkeypatch) -> None:
    response = client(monkeypatch).post(
        "/api/galiciana/investigaciones/informe",
        json={},
        headers={"X-ROB-Key": f"  {TEST_KEY}  "},
    )
    assert response.status_code == 422
    assert response.json() == {"error": "Falta investigation_id."}


def test_mcp_transport_is_protected(monkeypatch) -> None:
    with client(monkeypatch) as http:
        missing = http.post("/mcp", json={})
        correct = http.post("/mcp", json={}, headers={"X-ROB-Key": TEST_KEY})
        assert missing.status_code == 401
        assert missing.json() == {"error": "No autorizado."}
        assert correct.status_code not in {401, 503}


def test_openapi_declares_action_key_without_changing_operation_ids(monkeypatch) -> None:
    schema = client(monkeypatch, configured=False).get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["RobActionKey"] == {
        "type": "apiKey",
        "in": "header",
        "name": "X-ROB-Key",
    }
    old_ids = {
        "crearInvestigacionGaliciana",
        "procesarInvestigacionGaliciana",
        "obtenerInformeGaliciana",
        "crearInvestigacionFamiliarGaliciana",
        "leerPaginaGaliciana",
        "investigarPersonaGaliciana",
    }
    new_ids = {
        "crearInvestigacion",
        "procesarInvestigacion",
        "obtenerInformeInvestigacion",
        "leerFuenteInvestigacion",
    }
    operations = [path["post"] for path in schema["paths"].values()]
    operation_ids = {operation["operationId"] for operation in operations}
    assert old_ids <= operation_ids
    assert new_ids <= operation_ids
    assert operation_ids == old_ids | new_ids
    assert all(operation["security"] == [{"RobActionKey": []}] for operation in operations)
    for operation_id in new_ids:
        operation = next(item for item in operations if item["operationId"] == operation_id)
        response = operation["responses"]["200"]["content"]["application/json"][
            "schema"
        ]
        assert response.get("required") or len(response.get("properties", {})) >= 5

    report_request = schema["paths"]["/api/investigacion/informe"]["post"][
        "requestBody"
    ]["content"]["application/json"]["schema"]
    assert report_request["properties"]["modo"]["enum"] == ["compacto", "completo"]


def test_secret_never_appears_in_responses_or_logs(monkeypatch, caplog) -> None:
    http = client(monkeypatch)
    responses = [
        http.get("/health"),
        http.get("/openapi.json"),
        http.post(
            "/api/galiciana/investigaciones/informe",
            json={},
            headers={"X-ROB-Key": TEST_KEY},
        ),
    ]
    assert all(TEST_KEY not in response.text for response in responses)
    assert TEST_KEY not in caplog.text


def test_estado_reports_only_boolean_security_state(monkeypatch) -> None:
    client(monkeypatch)
    result = server.estado()
    assert result["seguridad"] == {
        "autenticacion_configurada": True,
        "rutas_operativas_protegidas": True,
    }
    assert TEST_KEY not in str(result)
