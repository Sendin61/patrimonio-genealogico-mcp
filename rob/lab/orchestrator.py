from __future__ import annotations

import asyncio
import time
from typing import Any

from .ai import AIProvider, interpret_research_request
from .bridge import BridgeCommand, BridgeCommandType, BridgeResult, InMemoryBridgeQueue
from .planner import ResearchPlan, SearchAction, build_research_plan
from .store import LabStore


class BridgeTimeoutError(TimeoutError):
    pass


class BridgeRunner:
    def __init__(self, bridge: InMemoryBridgeQueue) -> None:
        self.bridge = bridge

    async def call(
        self,
        command_type: BridgeCommandType,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = 90.0,
    ) -> BridgeResult:
        command = self.bridge.enqueue(
            BridgeCommand(type=command_type, payload=payload or {})
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.bridge.pop_result(command.id)
            if result is not None:
                return result
            await asyncio.sleep(0.1)
        raise BridgeTimeoutError(f"Timeout esperando la extensión FamilySearch ({command_type.value}).")


class ResearchOrchestrator:
    """First end-to-end local research loop.

    The orchestrator deliberately separates local AI interpretation from deterministic
    search planning and browser execution. No paid provider is assumed.
    """

    def __init__(
        self,
        *,
        provider: AIProvider,
        bridge: InMemoryBridgeQueue,
        store: LabStore,
        pause_between_queries: float = 1.25,
    ) -> None:
        self.provider = provider
        self.bridge = bridge
        self.runner = BridgeRunner(bridge)
        self.store = store
        self.pause_between_queries = max(0.75, pause_between_queries)

    async def start(self, raw_request: str) -> str:
        investigation_id = self.store.create_investigation(raw_request)
        asyncio.create_task(self.run(investigation_id, raw_request))
        return investigation_id

    async def run(self, investigation_id: str, raw_request: str) -> None:
        try:
            self.store.update_investigation(investigation_id, status="interpreting")
            self.store.add_event(investigation_id, "interpreting", {})
            interpretation = await interpret_research_request(self.provider, raw_request)

            plan = build_research_plan(interpretation)
            self.store.update_investigation(
                investigation_id,
                status="planned",
                interpretation=interpretation,
                plan=plan.to_dict(),
            )
            self.store.add_event(
                investigation_id,
                "planned",
                {
                    "actions": len(plan.actions),
                    "ocr_tolerant": plan.ocr_tolerant,
                    "multipage_required": plan.multipage_required,
                },
            )

            if not self.bridge.connected:
                self.store.update_investigation(investigation_id, status="waiting_familysearch")
                self.store.add_event(
                    investigation_id,
                    "waiting_familysearch",
                    {"message": "Abre FamilySearch e inicia/activa la extensión ROB."},
                )
                deadline = time.monotonic() + 300
                while time.monotonic() < deadline and not self.bridge.connected:
                    await asyncio.sleep(0.5)
                if not self.bridge.connected:
                    self.store.update_investigation(investigation_id, status="paused_familysearch")
                    self.store.add_event(
                        investigation_id,
                        "paused_familysearch",
                        {"message": "No apareció el puente FamilySearch; el expediente queda guardado."},
                    )
                    return

            self.store.update_investigation(investigation_id, status="searching")
            await self._run_initial_search(investigation_id, plan)
            self.store.update_investigation(investigation_id, status="search_phase_complete")
            self.store.add_event(
                investigation_id,
                "search_phase_complete",
                {
                    "unique_items": self.store.source_item_count(
                        investigation_id, source="familysearch"
                    ),
                    "next": "rank_candidates_and_build_multipage_context",
                },
            )
        except Exception as exc:
            self.store.update_investigation(investigation_id, status="error")
            self.store.add_event(
                investigation_id,
                "error",
                {"type": type(exc).__name__, "message": str(exc)},
            )

    async def _run_initial_search(self, investigation_id: str, plan: ResearchPlan) -> None:
        fulltext_actions = [action for action in plan.actions if action.kind == "fulltext_search"]
        total_actions = len(fulltext_actions)
        for index, action in enumerate(fulltext_actions, start=1):
            await self._run_fulltext_action(
                investigation_id,
                action,
                position=index,
                total=total_actions,
            )
            if index < total_actions:
                await asyncio.sleep(self.pause_between_queries)

    async def _run_fulltext_action(
        self,
        investigation_id: str,
        action: SearchAction,
        *,
        position: int,
        total: int,
    ) -> None:
        self.store.add_event(
            investigation_id,
            "query_started",
            {
                "position": position,
                "total": total,
                "phase": action.phase,
                "priority": action.priority,
                "query": action.query,
                "reason": action.reason,
            },
        )

        result = await self.runner.call(
            BridgeCommandType.FULLTEXT_SEARCH,
            {"query": action.query, "offset": 0, "count": 100},
        )
        if not result.ok:
            self.store.add_event(
                investigation_id,
                "query_failed",
                {"query": action.query, "error": result.error or "error desconocido"},
            )
            return

        payload = result.payload or {}
        data = payload.get("json") if isinstance(payload.get("json"), dict) else {}
        entries = data.get("entries") if isinstance(data.get("entries"), list) else []
        total_results = data.get("results")
        stored = 0
        with_ocr = 0

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            source_key = str(entry.get("id") or entry.get("sourceUrl") or "").strip()
            if not source_key:
                continue
            self.store.upsert_source_item(
                investigation_id,
                source="familysearch",
                source_key=source_key,
                item_type="fulltext_result",
                payload={
                    "query": action.query,
                    "phase": action.phase,
                    "priority": action.priority,
                    "entry": entry,
                },
            )
            stored += 1

            content = entry.get("content") if isinstance(entry.get("content"), dict) else {}
            raw_ocr = str(content.get("textDocument") or "")
            if raw_ocr:
                self.store.upsert_ocr_page(
                    source="familysearch",
                    image_id=source_key,
                    raw_ocr=raw_ocr,
                    ark=str(entry.get("sourceUrl") or "") or None,
                    metadata={
                        "collectionId": entry.get("collectionId"),
                        "collectionTitle": entry.get("collectionTitle"),
                        "recordDate": content.get("recordDate"),
                        "recordType": content.get("recordType"),
                        "recordPlace": content.get("recordPlace"),
                        "title": content.get("title"),
                        "entities": content.get("entities"),
                        "highlightTexts": content.get("highlightTexts"),
                    },
                )
                with_ocr += 1

        self.store.add_event(
            investigation_id,
            "query_completed",
            {
                "query": action.query,
                "phase": action.phase,
                "returned": len(entries),
                "stored": stored,
                "with_ocr": with_ocr,
                "familysearch_total": total_results,
            },
        )
