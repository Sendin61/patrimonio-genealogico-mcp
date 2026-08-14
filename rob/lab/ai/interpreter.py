from __future__ import annotations

from typing import Any

from .base import AIProvider, chat_json


INTERPRETATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "raw_request": {"type": "string"},
        "intent": {"type": "string"},
        "objectives": {"type": "array", "items": {"type": "string"}},
        "target_candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "value": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string"},
                },
                "required": ["value", "confidence", "reason"],
            },
        },
        "people_mentions": {"type": "array", "items": {"type": "string"}},
        "places": {"type": "array", "items": {"type": "string"}},
        "chronology": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "year_from": {"type": ["integer", "null"]},
                "year_to": {"type": ["integer", "null"]},
                "around": {"type": ["integer", "null"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["year_from", "year_to", "around", "confidence"],
        },
        "family_scope": {"type": "array", "items": {"type": "string"}},
        "ocr_tolerant": {"type": "boolean"},
        "multipage_required": {"type": "boolean"},
        "uncertainties": {"type": "array", "items": {"type": "string"}},
        "proposed_actions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "raw_request",
        "intent",
        "objectives",
        "target_candidates",
        "people_mentions",
        "places",
        "chronology",
        "family_scope",
        "ocr_tolerant",
        "multipage_required",
        "uncertainties",
        "proposed_actions",
    ],
}


SYSTEM_PROMPT = """Eres el intérprete de peticiones de ROB Genealogy Lab.
El usuario escribe genealogía en español de manera libre, rápida y con posibles faltas.
Tu trabajo NO es resolver todavía la genealogía: convierte la petición en un objetivo de
investigación estructurado.

Reglas obligatorias:
1. Conserva raw_request exactamente como se recibió.
2. Tolera faltas y abreviaturas, pero una corrección probable es un CANDIDATO, no un hecho.
3. Si 'barela' probablemente significa 'Varela', incluye Varela como candidato con confianza
   y explica el motivo; no borres la forma observada.
4. No inventes fechas, parentescos, lugares ni nombres que el usuario no haya dado o que no
   sean una normalización lingüística claramente marcada como candidata.
5. Distingue lo seguro de lo dudoso mediante confidence y uncertainties.
6. ocr_tolerant debe ser true cuando la petición mencione OCR, errores, variantes o cuando una
   búsqueda genealógica histórica se beneficie razonablemente de tolerancia a OCR.
7. multipage_required debe ser true para cualquier análisis de un documento que pueda contener
   una coincidencia relevante. ROB debe leer varias páginas y reconstruir los límites del
   documento antes de formular hipótesis fuertes.
8. proposed_actions describe pasos de investigación, no conclusiones.
9. Devuelve exclusivamente JSON válido conforme al esquema solicitado.
"""


async def interpret_research_request(provider: AIProvider, raw_request: str) -> dict[str, Any]:
    text = str(raw_request)
    if not text.strip():
        raise ValueError("Research request cannot be empty")
    value = await chat_json(
        provider,
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=0.0,
        max_tokens=1800,
        json_schema=INTERPRETATION_SCHEMA,
    )
    # The model is not allowed to rewrite the audit copy of what the user typed.
    value["raw_request"] = text
    value["multipage_required"] = True
    return value
