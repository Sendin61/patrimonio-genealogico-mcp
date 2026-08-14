from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .ai.base import AIProvider, chat_json
from .models import DocumentContext
from .ocr_resolver import multipage_excerpt


SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "relevance": {"type": "number", "minimum": 0, "maximum": 1},
        "summary": {"type": "string"},
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "observed_name": {"type": "string"},
                    "interpreted_name": {"type": ["string", "null"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "pages": {"type": "array", "items": {"type": "integer"}},
                    "role": {"type": ["string", "null"]},
                    "evidence": {"type": "string"},
                },
                "required": ["observed_name", "interpreted_name", "confidence", "pages", "role", "evidence"],
            },
        },
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "subject": {"type": "string"},
                    "relation": {"type": "string"},
                    "object": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "pages": {"type": "array", "items": {"type": "integer"}},
                    "evidence": {"type": "string"},
                },
                "required": ["subject", "relation", "object", "confidence", "pages", "evidence"],
            },
        },
        "ocr_suspicions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "raw_ocr": {"type": "string"},
                    "candidate": {"type": ["string", "null"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "pages": {"type": "array", "items": {"type": "integer"}},
                    "reason": {"type": "string"},
                    "needs_visual_check": {"type": "boolean"},
                },
                "required": ["raw_ocr", "candidate", "confidence", "pages", "reason", "needs_visual_check"],
            },
        },
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "pages": {"type": "array", "items": {"type": "integer"}},
                    "evidence": {"type": "string"},
                    "caveat": {"type": "string"},
                },
                "required": ["claim", "confidence", "pages", "evidence", "caveat"],
            },
        },
        "needs_more_context": {"type": "boolean"},
        "more_context_direction": {"type": ["string", "null"], "enum": ["before", "after", "both", null]},
    },
    "required": [
        "relevance",
        "summary",
        "people",
        "relationships",
        "ocr_suspicions",
        "hypotheses",
        "needs_more_context",
        "more_context_direction",
    ],
}


SYSTEM = """Eres el analista documental de ROB Genealogy Lab. Recibes OCR histórico de
VARIAS páginas que forman probablemente un mismo documento, más el objetivo del expediente.

Reglas:
- El OCR puede estar gravemente corrupto.
- Lee el documento como un conjunto, no página por página de forma aislada.
- No conviertas una coincidencia de nombre en identidad segura.
- Diferencia texto observado de interpretaciones.
- Si una palabra/nombre parece destrozado por OCR, conserva raw_ocr y propone candidate solo
  cuando el contexto multipágina, parentesco, repeticiones o conocimiento del expediente lo apoyen.
- Una gran distancia ortográfica no invalida por sí sola una reconstrucción contextual.
- Toda relación o hipótesis debe citar las páginas (números [[IMG ...]]) que la sostienen.
- evidence debe ser breve y fiel al OCR; no inventes una cita limpia que no esté en el texto.
- Si el documento parece empezar antes o continuar después, solicita más contexto.
- Nada de esta salida es un hecho aceptado automáticamente: son interpretaciones/hipótesis.
Devuelve exclusivamente JSON conforme al esquema."""


@dataclass(slots=True)
class DocumentAnalyzer:
    provider: AIProvider

    async def analyze(
        self,
        *,
        context: DocumentContext,
        interpretation: dict[str, Any],
        rank_reasons: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        target_terms = []
        for item in interpretation.get("target_candidates", []) or []:
            value = item.get("value") if isinstance(item, dict) else item
            if str(value or "").strip():
                target_terms.append(str(value).strip())
        target_terms.extend(str(x) for x in interpretation.get("people_mentions", []) or [])
        target_terms.extend(str(x) for x in interpretation.get("places", []) or [])
        excerpt = multipage_excerpt(
            context,
            focus_terms=target_terms,
            maximum_characters=60000,
        )
        user = (
            "OBJETIVO ESTRUCTURADO DEL EXPEDIENTE:\n"
            f"{interpretation}\n\n"
            "MOTIVOS DEL RANKING PREVIO (heurística, no evidencia):\n"
            f"{rank_reasons or []}\n\n"
            f"DOCUMENTO ESTIMADO: imágenes {context.estimated_start_image}-{context.estimated_end_image}\n"
            "OCR MULTIPÁGINA:\n"
            f"{excerpt}"
        )
        return await chat_json(
            self.provider,
            [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=3500,
            json_schema=SCHEMA,
        )
