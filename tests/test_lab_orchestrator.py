from __future__ import annotations

import asyncio
import json
import time

from rob.lab.ai.base import AIProviderStatus
from rob.lab.bridge import BridgeResult, InMemoryBridgeQueue
from rob.lab.orchestrator import ResearchOrchestrator
from rob.lab.store import LabStore


class FakeProvider:
    async def status(self) -> AIProviderStatus:
        return AIProviderStatus(True, "fake", "http://local", "fake-model")

    async def chat(self, messages, *, temperature=0.1, max_tokens=2048, json_schema=None):
        return json.dumps(
            {
                "raw_request": "ignored",
                "intent": "find_person",
                "objectives": ["buscar Ana Pérez"],
                "target_candidates": [
                    {"value": "Ana Pérez", "confidence": 0.95, "reason": "literal"}
                ],
                "people_mentions": [],
                "places": [],
                "chronology": {
                    "year_from": None,
                    "year_to": None,
                    "around": None,
                    "confidence": 0.0,
                },
                "family_scope": [],
                "ocr_tolerant": False,
                "multipage_required": True,
                "uncertainties": [],
                "proposed_actions": ["buscar"],
            },
            ensure_ascii=False,
        )


async def _scenario(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROB_LAB_HOME", str(tmp_path / "ROB-Genealogy-Lab"))
    store = LabStore()
    bridge = InMemoryBridgeQueue()
    bridge.last_extension_seen_at = time.time()
    stop = asyncio.Event()

    async def fake_extension() -> None:
        while not stop.is_set():
            command = bridge.next_command()
            if command is None:
                await asyncio.sleep(0.01)
                continue

            if command.type.value == "fulltext_search":
                query = command.payload.get("query")
                payload = {
                    "json": {
                        "results": 1,
                        "entries": [
                            {
                                "id": "3:1:TEST-ANA",
                                "sourceUrl": "https://www.familysearch.org/ark:/61903/3:1:TEST-ANA",
                                "collectionId": "TEST",
                                "collectionTitle": "Colección de prueba",
                                "content": {
                                    "recordDate": "1800",
                                    "recordType": "Notarial",
                                    "recordPlace": "Arzúa",
                                    "title": "Documento de prueba",
                                    "textDocument": f"Ana Pérez aparece en este documento. Consulta {query}",
                                    "entities": [],
                                    "highlightTexts": ["Ana Pérez"],
                                },
                            }
                        ],
                    }
                }
            else:
                # Deliberately simulate a candidate whose structured OCR is unavailable.
                # The orchestrator must keep the search result and complete gracefully.
                payload = {}

            bridge.complete(
                BridgeResult(
                    command_id=command.id,
                    ok=True,
                    payload=payload,
                )
            )

    responder = asyncio.create_task(fake_extension())
    orchestrator = ResearchOrchestrator(
        provider=FakeProvider(),
        bridge=bridge,
        store=store,
        pause_between_queries=0.01,
    )
    orchestrator.pause_between_queries = 0.0
    investigation_id = store.create_investigation("busca ana perez")
    await orchestrator.run(investigation_id, "busca ana perez")
    stop.set()
    await responder

    investigation = store.investigation(investigation_id)
    assert investigation is not None
    assert investigation["status"] == "analysis_phase_complete"
    assert store.source_item_count(investigation_id, source="familysearch") == 1
    assert store.search_ocr('"Ana"')
    kinds = [event["kind"] for event in store.events(investigation_id)]
    assert "planned" in kinds
    assert "query_completed" in kinds
    assert "candidates_ranked" in kinds
    assert "context_unavailable" in kinds
    assert "analysis_phase_complete" in kinds


def test_end_to_end_orchestrator(monkeypatch, tmp_path) -> None:
    asyncio.run(_scenario(tmp_path, monkeypatch))
