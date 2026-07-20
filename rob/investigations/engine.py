from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from .models import InvestigationTarget
from .sources import GenealogicalSourceAdapter
from .store import UniversalInvestigationStore


TERMINAL_SOURCE_STATUSES = {"complete", "failed"}


def _diagnostic(exc: BaseException, *, operation: str) -> dict[str, str]:
    message = str(exc).strip() or repr(exc)
    return {
        "operacion": operation,
        "tipo": type(exc).__name__,
        "mensaje": message,
    }


class UniversalInvestigationEngine:
    """Persisted coordinator over explicitly registered source adapters."""

    def __init__(
        self,
        store: UniversalInvestigationStore,
        adapters: Iterable[GenealogicalSourceAdapter],
    ) -> None:
        self.store = store
        self.adapters = {adapter.source_name: adapter for adapter in adapters}

    @property
    def available_sources(self) -> list[str]:
        return [name for name, adapter in self.adapters.items() if adapter.available]

    def validate_sources(self, sources: Iterable[str]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for raw_name in sources:
            name = str(raw_name).strip().casefold()
            if not name or name in seen:
                continue
            if name not in self.adapters or not self.adapters[name].available:
                raise ValueError(f"Fuente desconocida o no disponible: {name}.")
            seen.add(name)
            output.append(name)
        return output

    @staticmethod
    def _overall_status(runs: list[dict[str, Any]]) -> str:
        if not runs:
            return "failed"
        statuses = [run["status"] for run in runs]
        if all(status == "complete" for status in statuses):
            return "complete"
        if all(status in TERMINAL_SOURCE_STATUSES for status in statuses):
            return "partial" if "complete" in statuses else "failed"
        return "processing"

    def _refresh_status(self, investigation_id: str) -> str:
        status = self._overall_status(self.store.source_runs(investigation_id))
        self.store.update_status(investigation_id, status)
        return status

    @staticmethod
    def _source_summary(run: dict[str, Any]) -> dict[str, Any]:
        return {
            "fuente": run["source_name"],
            "estado": run["status"],
            "investigation_id_fuente": run.get("source_investigation_id"),
            "diagnosticos": run.get("diagnostics", []),
        }

    async def create_investigation(
        self,
        target: InvestigationTarget,
        *,
        requested_sources: list[str] | None = None,
        maximum_queries: int = 8,
        maximum_results_per_source: int = 80,
    ) -> dict[str, Any]:
        if not target.name.strip():
            raise ValueError("Falta el campo obligatorio nombre.")
        sources = self.validate_sources(requested_sources or ["galiciana"])
        if not sources:
            sources = self.validate_sources(["galiciana"])
        investigation_id = self.store.create(target, sources)

        for source_name in sources:
            self.store.ensure_source_run(investigation_id, source_name)

        for source_name in sources:
            adapter = self.adapters[source_name]
            try:
                result = await adapter.create_investigation(
                    target,
                    maximum_queries=max(1, maximum_queries),
                    maximum_results=max(1, maximum_results_per_source),
                )
                source_id = str(result.get("source_investigation_id") or "").strip()
                if not source_id:
                    raise RuntimeError("La fuente no devolvió un identificador de expediente.")
                self.store.update_run(
                    investigation_id,
                    source_name,
                    status=str(result.get("status") or "processing"),
                    source_investigation_id=source_id,
                    diagnostics=list(result.get("diagnostics") or []),
                )
            except Exception as exc:
                self.store.update_run(
                    investigation_id,
                    source_name,
                    status="failed",
                    diagnostics=[_diagnostic(exc, operation="crear")],
                )

        status = self._refresh_status(investigation_id)
        return {
            "investigation_id": investigation_id,
            "persona_objetivo": target.name,
            "estado": status,
            "fuentes_solicitadas": sources,
            "fuentes": [
                self._source_summary(run)
                for run in self.store.source_runs(investigation_id)
            ],
            "siguiente_paso": (
                "Llama a procesarInvestigacion."
                if status == "processing"
                else "Llama a obtenerInformeInvestigacion."
            ),
        }

    async def _ensure_child(
        self,
        investigation: dict[str, Any],
        run: dict[str, Any],
    ) -> dict[str, Any]:
        if run.get("source_investigation_id"):
            return run
        adapter = self.adapters[run["source_name"]]
        result = await adapter.create_investigation(
            investigation["target"], maximum_queries=8, maximum_results=80
        )
        source_id = str(result.get("source_investigation_id") or "").strip()
        if not source_id:
            raise RuntimeError("La fuente no devolvió un identificador de expediente.")
        return self.store.update_run(
            investigation["id"],
            run["source_name"],
            status=str(result.get("status") or "processing"),
            source_investigation_id=source_id,
            diagnostics=[],
        )

    def _mark_budget_exhausted(
        self,
        investigation_id: str,
        runs: list[dict[str, Any]],
        *,
        first_source_started: bool,
    ) -> list[dict[str, Any]]:
        updates: list[tuple[str, str, list[dict[str, Any]]]] = []
        for index, run in enumerate(runs):
            started = first_source_started and index == 0
            operation = (
                "presupuesto_agotado"
                if started
                else "presupuesto_agotado_antes_de_iniciar"
            )
            message = (
                "Presupuesto global agotado durante la fuente."
                if started
                else "Presupuesto global agotado antes de iniciar la fuente."
            )
            updates.append(
                (
                    run["source_name"],
                    "pending",
                    [
                        *run.get("diagnostics", []),
                        _diagnostic(TimeoutError(message), operation=operation),
                    ],
                )
            )
        updated = self.store.update_runs(investigation_id, updates)
        return [self._source_summary(updated[run["source_name"]]) for run in runs]

    async def process(
        self,
        investigation_id: str,
        *,
        batch_size: int = 5,
        time_budget_seconds: int = 50,
        sources: list[str] | None = None,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        budget = min(max(float(time_budget_seconds), 0.0), 55.0)
        deadline = loop.time() + budget
        investigation = self.store.require(investigation_id)
        requested = list(investigation["requested_sources"])
        selected = self.validate_sources(sources or [])
        if selected:
            unrequested = [name for name in selected if name not in requested]
            if unrequested:
                raise ValueError(
                    f"La fuente no pertenece a este expediente: {unrequested[0]}."
                )
        runs = self.store.source_runs(investigation_id)
        selected_set = set(selected)
        open_runs = [
            run
            for run in runs
            if (not selected_set or run["source_name"] in selected_set)
            and (run["status"] not in TERMINAL_SOURCE_STATUSES or bool(selected_set))
        ]
        details: list[dict[str, Any]] = []

        for run_index, run in enumerate(open_runs):
            source_name = run["source_name"]
            previous_diagnostics = list(run.get("diagnostics", []))
            remaining = deadline - loop.time()
            if remaining <= 0:
                details.extend(
                    self._mark_budget_exhausted(
                        investigation_id,
                        open_runs[run_index:],
                        first_source_started=False,
                    )
                )
                break
            try:
                async with asyncio.timeout(remaining):
                    run = await self._ensure_child(investigation, run)
                    if run["status"] == "complete":
                        details.append(self._source_summary(run))
                        continue
                    self.store.update_run(
                        investigation_id, source_name, status="processing"
                    )
                    result = await self.adapters[source_name].process_next_batch(
                        str(run["source_investigation_id"]),
                        batch_size=max(1, batch_size),
                        time_budget_seconds=remaining,
                    )
                updated = self.store.update_run(
                    investigation_id,
                    source_name,
                    status=str(result.get("status") or "processing"),
                    diagnostics=previous_diagnostics,
                )
                detail = self._source_summary(updated)
                detail["detalle_fuente"] = result.get("detail", result)
                details.append(detail)
            except TimeoutError:
                details.extend(
                    self._mark_budget_exhausted(
                        investigation_id,
                        open_runs[run_index:],
                        first_source_started=True,
                    )
                )
                break
            except Exception as exc:
                updated = self.store.update_run(
                    investigation_id,
                    source_name,
                    status="failed",
                    diagnostics=[
                        *previous_diagnostics,
                        _diagnostic(exc, operation="procesar"),
                    ],
                )
                details.append(self._source_summary(updated))
            except BaseException as exc:
                self.store.update_run(
                    investigation_id,
                    source_name,
                    status="pending",
                    diagnostics=[
                        *previous_diagnostics,
                        _diagnostic(exc, operation="procesar_interrumpido"),
                    ],
                )
                self._refresh_status(investigation_id)
                raise

        status = self._refresh_status(investigation_id)
        return {
            "investigation_id": investigation_id,
            "estado": status,
            "completada": status in {"complete", "partial", "failed"},
            "fuentes": details,
            "siguiente_paso": (
                "Llama otra vez a procesarInvestigacion."
                if status == "processing"
                else "Llama a obtenerInformeInvestigacion."
            ),
        }

    def _collect_source_report(
        self,
        source_name: str,
        source_id: str,
        *,
        required_per_kind: int,
        include_pending: bool,
    ) -> dict[str, Any]:
        """Read each source from zero; source order is preserved for global paging."""
        evidence: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []
        cursor = 0
        report: dict[str, Any] = {}
        evidence_total = 0
        pending_total = 0

        while True:
            page = self.adapters[source_name].get_report(
                source_id,
                offset=cursor,
                maximum_results=min(40, max(1, required_per_kind)),
                include_pending=include_pending,
            )
            if not report:
                report = dict(page)
                relations = list(
                    page.get("familia_documentada_o_candidata", [])
                )
            evidence.extend(page.get("evidencias_documentales", []))
            if include_pending:
                pending.extend(page.get("paginas_no_leidas", []))

            total = max(
                len(evidence) + len(pending),
                int(page.get("total_resultados") or 0),
            )
            evidence_total = max(
                len(evidence), int(page.get("paginas_leidas") or 0)
            )
            pending_total = (
                max(len(pending), total - evidence_total) if include_pending else 0
            )
            evidence_target = min(required_per_kind, evidence_total)
            pending_target = min(required_per_kind, pending_total)
            if len(evidence) >= evidence_target and len(pending) >= pending_target:
                break

            pagination = page.get("paginacion") or {}
            returned = int(
                pagination.get("devueltos")
                or len(page.get("evidencias_documentales", []))
                + len(page.get("paginas_no_leidas", []))
            )
            next_cursor = pagination.get("siguiente_desde")
            if next_cursor is None and returned:
                next_cursor = cursor + returned
            if next_cursor is None or int(next_cursor) <= cursor or int(next_cursor) >= total:
                break
            cursor = int(next_cursor)

        report["evidencias_documentales"] = evidence
        report["paginas_no_leidas"] = pending
        report["familia_documentada_o_candidata"] = relations
        report["total_evidencias"] = evidence_total
        report["total_pendientes"] = pending_total
        return report

    def report(
        self,
        investigation_id: str,
        *,
        offset: int = 0,
        maximum_results: int = 20,
        include_pending: bool = True,
    ) -> dict[str, Any]:
        investigation = self.store.require(investigation_id)
        runs = self.store.source_runs(investigation_id)
        source_order = {
            name: index
            for index, name in enumerate(investigation["requested_sources"])
        }
        runs.sort(key=lambda run: source_order.get(run["source_name"], len(runs)))
        evidence: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []
        source_coverage: list[dict[str, Any]] = []
        offset = max(0, offset)
        maximum_results = max(1, maximum_results)
        required_per_kind = offset + maximum_results
        total_evidence = 0
        total_pending = 0

        for run in runs:
            source_name = run["source_name"]
            source_id = run.get("source_investigation_id")
            coverage = self._source_summary(run)
            if source_id:
                try:
                    source_report = self._collect_source_report(
                        source_name,
                        str(source_id),
                        required_per_kind=required_per_kind,
                        include_pending=include_pending,
                    )
                    total_evidence += int(source_report.get("total_evidencias") or 0)
                    total_pending += int(source_report.get("total_pendientes") or 0)
                    coverage.update(
                        {
                            "cobertura": source_report.get("cobertura"),
                            "total_resultados": source_report.get("total_resultados"),
                            "elementos_procesados": source_report.get("paginas_leidas"),
                            "elementos_pendientes": source_report.get("paginas_pendientes"),
                            "elementos_fallidos": source_report.get("paginas_fallidas"),
                        }
                    )
                    source_diagnostics = list(
                        source_report.get("diagnosticos_fuente", [])
                    )
                    if source_diagnostics:
                        coverage["diagnosticos"] = [
                            *coverage.get("diagnosticos", []),
                            *source_diagnostics,
                        ]
                    for item in source_report.get("evidencias_documentales", []):
                        evidence.append(
                            {
                                **item,
                                "fuente": source_name,
                                "investigation_id_fuente": source_id,
                                "fecha": item.get("fecha"),
                                "titulo": item.get("titulo"),
                                "url_pagina": item.get("url_pagina"),
                                "contexto": item.get("contexto"),
                            }
                        )
                    if include_pending:
                        for item in source_report.get("paginas_no_leidas", []):
                            pending.append(
                                {
                                    **item,
                                    "fuente": source_name,
                                    "investigation_id_fuente": source_id,
                                }
                            )
                    for item in source_report.get(
                        "familia_documentada_o_candidata", []
                    ):
                        relations.append(
                            {
                                **item,
                                "fuente": source_name,
                                "investigation_id_fuente": source_id,
                            }
                        )
                except Exception as exc:
                    diagnostics = [
                        *coverage.get("diagnosticos", []),
                        _diagnostic(exc, operation="informe"),
                    ]
                    coverage["diagnosticos"] = diagnostics
                    self.store.update_run(
                        investigation_id,
                        source_name,
                        status=run["status"],
                        diagnostics=diagnostics,
                    )
            source_coverage.append(coverage)

        runs = self.store.source_runs(investigation_id)
        evidence = evidence[offset : offset + maximum_results]
        pending = (
            pending[offset : offset + maximum_results] if include_pending else []
        )
        return {
            "investigation_id": investigation_id,
            "persona_objetivo": investigation["target"].name,
            "estado": investigation["status"],
            "creada": investigation["created_at"],
            "actualizada": investigation["updated_at"],
            "fuentes_solicitadas": investigation["requested_sources"],
            "cobertura_por_fuente": source_coverage,
            "evidencias_documentales": evidence,
            "elementos_pendientes": pending,
            "relaciones_familiares_documentadas": relations,
            "diagnosticos_por_fuente": {
                run["source_name"]: run.get("diagnostics", []) for run in runs
            },
            "paginacion": {
                "desde": offset,
                "devueltos": len(evidence),
                "max_resultados": maximum_results,
                "siguiente_desde": (
                    offset + len(evidence)
                    if offset + len(evidence) < total_evidence
                    else None
                ),
                "total_evidencias": total_evidence,
                "devueltos_pendientes": len(pending),
                "total_pendientes": total_pending,
                "siguiente_desde_pendientes": (
                    offset + len(pending)
                    if include_pending and offset + len(pending) < total_pending
                    else None
                ),
            },
            "reglas_de_evidencia": {
                "datos_objetivo": "aportados por el usuario; no son hallazgos",
                "texto_documental": "se conserva literalmente, sin corregir el OCR",
                "trazabilidad": "cada evidencia conserva su fuente e ID interno",
                "orden_global": (
                    "orden de fuentes solicitado y, dentro de cada fuente, "
                    "orden estable de su informe"
                ),
            },
        }

    async def read_source(
        self,
        source_name: str,
        source_url: str,
        *,
        terms: list[str] | None = None,
        maximum_characters: int = 12000,
    ) -> dict[str, Any]:
        source = self.validate_sources([source_name])
        if not source_url.strip():
            raise ValueError("Falta el campo obligatorio url.")
        return await self.adapters[source[0]].read_source(
            source_url.strip(),
            terms=terms or [],
            maximum_characters=max(1, maximum_characters),
        )
