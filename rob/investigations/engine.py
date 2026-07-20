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

    async def process(
        self,
        investigation_id: str,
        *,
        batch_size: int = 5,
        time_budget_seconds: int = 50,
        sources: list[str] | None = None,
    ) -> dict[str, Any]:
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
        budget = max(1, min(int(time_budget_seconds), 55))
        per_source_budget = max(1, budget // max(1, len(open_runs)))
        details: list[dict[str, Any]] = []

        for run in open_runs:
            source_name = run["source_name"]
            previous_diagnostics = list(run.get("diagnostics", []))
            try:
                async with asyncio.timeout(per_source_budget):
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
                        time_budget_seconds=per_source_budget,
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
            except TimeoutError as exc:
                updated = self.store.update_run(
                    investigation_id,
                    source_name,
                    status="pending",
                    diagnostics=[
                        *previous_diagnostics,
                        _diagnostic(exc, operation="presupuesto_agotado"),
                    ],
                )
                details.append(self._source_summary(updated))
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
        evidence: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []
        source_coverage: list[dict[str, Any]] = []

        for run in runs:
            source_name = run["source_name"]
            source_id = run.get("source_investigation_id")
            coverage = self._source_summary(run)
            if source_id:
                try:
                    source_report = self.adapters[source_name].get_report(
                        str(source_id),
                        offset=max(0, offset),
                        maximum_results=max(1, maximum_results),
                        include_pending=include_pending,
                    )
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
        maximum_results = max(1, maximum_results)
        evidence = evidence[:maximum_results]
        pending = pending[:maximum_results] if include_pending else []
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
                "desde": max(0, offset),
                "devueltos": len(evidence),
                "max_resultados": maximum_results,
                "siguiente_desde": (
                    max(0, offset) + len(evidence)
                    if len(evidence) == maximum_results
                    else None
                ),
            },
            "reglas_de_evidencia": {
                "datos_objetivo": "aportados por el usuario; no son hallazgos",
                "texto_documental": "se conserva literalmente, sin corregir el OCR",
                "trazabilidad": "cada evidencia conserva su fuente e ID interno",
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
