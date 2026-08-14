from __future__ import annotations

import asyncio
import time
from typing import Any

from .ai import AIProvider, interpret_research_request
from .bridge import BridgeCommand, BridgeCommandType, BridgeResult, InMemoryBridgeQueue
from .document_analyzer import DocumentAnalyzer
from .familysearch_context import FamilySearchDocumentReader
from .planner import ResearchPlan, SearchAction, build_research_plan
from .ranker import RankedCandidate, rank_fulltext_entries
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
    """Local-first end-to-end genealogy research loop.

    The AI interprets and analyses; deterministic code plans, retrieves, ranks and stores.
    Paid remote AI is not assumed. Strong document hypotheses are generated only after a
    multi-page context attempt, never from an isolated search snippet by design.
    """

    def __init__(
        self,
        *,
        provider: AIProvider,
        bridge: InMemoryBridgeQueue,
        store: LabStore,
        pause_between_queries: float = 1.25,
        initial_deep_candidates: int = 4,
    ) -> None:
        self.provider = provider
        self.bridge = bridge
        self.runner = BridgeRunner(bridge)
        self.store = store
        self.pause_between_queries = max(0.75, pause_between_queries)
        self.initial_deep_candidates = max(1, min(initial_deep_candidates, 20))

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

            if not await self._wait_for_bridge(investigation_id):
                return

            self.store.update_investigation(investigation_id, status="searching")
            entries = await self._run_initial_search(investigation_id, plan)
            ranked = rank_fulltext_entries(list(entries.values()), interpretation)
            self._save_ranking_event(investigation_id, ranked)

            if not ranked:
                self.store.update_investigation(investigation_id, status="complete_no_candidates")
                self.store.add_event(
                    investigation_id,
                    "complete_no_candidates",
                    {"message": "La primera pasada no produjo candidatos analizables."},
                )
                return

            self.store.update_investigation(investigation_id, status="deep_analysis")
            deep = self._deep_selection(ranked)
            built = 0
            analyzed = 0
            analyzer = DocumentAnalyzer(self.provider)
            reader = FamilySearchDocumentReader(
                bridge_call=lambda kind, payload: self.runner.call(kind, payload, timeout=120.0),
                provider=self.provider,
                store=self.store,
                initial_radius=3,
                max_extra_pages_each_side=6,
            )

            for position, candidate in enumerate(deep, start=1):
                self.store.add_event(
                    investigation_id,
                    "context_started",
                    {
                        "position": position,
                        "total": len(deep),
                        "source_key": candidate.source_key,
                        "score": candidate.score,
                    },
                )
                try:
                    context = await reader.read(candidate.source_key)
                except Exception as exc:
                    self.store.add_event(
                        investigation_id,
                        "context_unavailable",
                        {
                            "source_key": candidate.source_key,
                            "score": candidate.score,
                            "error": str(exc),
                        },
                    )
                    continue

                built += 1
                context_payload = {
                    "center_image": context.center_image,
                    "estimated_start_image": context.estimated_start_image,
                    "estimated_end_image": context.estimated_end_image,
                    "pages": [
                        {
                            "image_number": page.image_number,
                            "ark": page.ark,
                            "text_length": len(page.raw_text),
                        }
                        for page in context.pages
                    ],
                    "boundary_reasons": context.reasons,
                    "ranking_score": candidate.score,
                    "ranking_reasons": [
                        {"points": reason.points, "reason": reason.reason, "evidence": reason.evidence}
                        for reason in candidate.reasons
                    ],
                }
                self.store.upsert_source_item(
                    investigation_id,
                    source="familysearch",
                    source_key=candidate.source_key,
                    item_type="document_context",
                    payload=context_payload,
                )
                self.store.add_event(
                    investigation_id,
                    "context_built",
                    {
                        "source_key": candidate.source_key,
                        "pages": len(context.pages),
                        "from": context.estimated_start_image,
                        "to": context.estimated_end_image,
                    },
                )

                try:
                    analysis = await analyzer.analyze(
                        context=context,
                        interpretation=interpretation,
                        rank_reasons=context_payload["ranking_reasons"],
                    )
                except Exception as exc:
                    self.store.add_event(
                        investigation_id,
                        "analysis_failed",
                        {"source_key": candidate.source_key, "error": str(exc)},
                    )
                    continue

                analyzed += 1
                self.store.upsert_source_item(
                    investigation_id,
                    source="familysearch",
                    source_key=candidate.source_key,
                    item_type="document_analysis",
                    payload=analysis,
                )
                self.store.add_event(
                    investigation_id,
                    "document_analyzed",
                    {
                        "source_key": candidate.source_key,
                        "relevance": analysis.get("relevance"),
                        "summary": str(analysis.get("summary") or "")[:700],
                        "relationships": len(analysis.get("relationships") or []),
                        "ocr_suspicions": len(analysis.get("ocr_suspicions") or []),
                        "hypotheses": len(analysis.get("hypotheses") or []),
                        "needs_more_context": bool(analysis.get("needs_more_context")),
                    },
                )

            self.store.update_investigation(investigation_id, status="analysis_phase_complete")
            self.store.add_event(
                investigation_id,
                "analysis_phase_complete",
                {
                    "unique_search_results": len(entries),
                    "ranked_candidates": len(ranked),
                    "deep_candidates": len(deep),
                    "multipage_contexts": built,
                    "documents_analyzed": analyzed,
                    "next": "adaptive_followup_search_and_identity_resolution",
                },
            )
        except Exception as exc:
            self.store.update_investigation(investigation_id, status="error")
            self.store.add_event(
                investigation_id,
                "error",
                {"type": type(exc).__name__, "message": str(exc)},
            )

    async def _wait_for_bridge(self, investigation_id: str) -> bool:
        if self.bridge.connected:
            return True
        self.store.update_investigation(investigation_id, status="waiting_familysearch")
        self.store.add_event(
            investigation_id,
            "waiting_familysearch",
            {"message": "Abre FamilySearch e inicia/activa la extensión ROB."},
        )
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline and not self.bridge.connected:
            await asyncio.sleep(0.5)
        if self.bridge.connected:
            return True
        self.store.update_investigation(investigation_id, status="paused_familysearch")
        self.store.add_event(
            investigation_id,
            "paused_familysearch",
            {"message": "No apareció el puente FamilySearch; el expediente queda guardado."},
        )
        return False

    def _deep_selection(self, ranked: list[RankedCandidate]) -> list[RankedCandidate]:
        strong = [candidate for candidate in ranked if candidate.score >= 45]
        pool = strong if strong else ranked
        return pool[: self.initial_deep_candidates]

    def _save_ranking_event(self, investigation_id: str, ranked: list[RankedCandidate]) -> None:
        self.store.add_event(
            investigation_id,
            "candidates_ranked",
            {
                "count": len(ranked),
                "top": [
                    {
                        "source_key": candidate.source_key,
                        "score": candidate.score,
                        "title": (
                            candidate.entry.get("content", {}).get("title")
                            if isinstance(candidate.entry.get("content"), dict)
                            else None
                        ),
                        "reasons": [
                            {"points": reason.points, "reason": reason.reason, "evidence": reason.evidence}
                            for reason in candidate.reasons[:5]
                        ],
                    }
                    for candidate in ranked[:20]
                ],
            },
        )

    async def _run_initial_search(
        self,
        investigation_id: str,
        plan: ResearchPlan,
    ) -> dict[str, dict[str, Any]]:
        fulltext_actions = [action for action in plan.actions if action.kind == "fulltext_search"]
        total_actions = len(fulltext_actions)
        unique: dict[str, dict[str, Any]] = {}
        for index, action in enumerate(fulltext_actions, start=1):
            entries = await self._run_fulltext_action(
                investigation_id,
                action,
                position=index,
                total=total_actions,
            )
            for entry in entries:
                source_key = str(entry.get("id") or entry.get("sourceUrl") or "").strip()
                if source_key:
                    unique[source_key] = entry
            if index < total_actions:
                await asyncio.sleep(self.pause_between_queries)
        return unique

    async def _run_fulltext_action(
        self,
        investigation_id: str,
        action: SearchAction,
        *,
        position: int,
        total: int,
    ) -> list[dict[str, Any]]:
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
            return []

        payload = result.payload or {}
        data = payload.get("json") if isinstance(payload.get("json"), dict) else {}
        raw_entries = data.get("entries") if isinstance(data.get("entries"), list) else []
        entries = [entry for entry in raw_entries if isinstance(entry, dict)]
        total_results = data.get("results")
        stored = 0
        with_ocr = 0

        for entry in entries:
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
                    ark=str(entry.get("sourceUrl") or "") or source_key,
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
        return entries
