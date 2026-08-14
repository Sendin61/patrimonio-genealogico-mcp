from __future__ import annotations

import re
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


SYSTEM_PROMPT = """Eres el intérprete RÁPIDO de ROB Genealogy Lab.
El usuario escribe genealogía en español de manera libre, rápida, con posibles faltas y puede
pegar perfiles completos con nombres, fechas, lugares y familiares.
Tu trabajo NO es resolver la genealogía ni razonar largamente: convierte la petición en un
objetivo estructurado para que otro motor investigue.

Reglas obligatorias:
1. Conserva raw_request exactamente como se recibió.
2. Tolera faltas y abreviaturas, pero una corrección probable es un CANDIDATO, no un hecho.
3. Si una forma como 'barela' probablemente significa 'Varela', conserva la forma observada y
   añade Varela solo como candidato explicado.
4. No inventes fechas, parentescos, lugares ni nombres.
5. Si el usuario pega un perfil, identifica como objetivo principal a la persona encabezada y
   coloca familiares nombrados en people_mentions.
6. Distingue lo seguro de lo dudoso mediante confidence y uncertainties.
7. ocr_tolerant=true para investigación histórica salvo razón clara en contra.
8. multipage_required=true: antes de hipótesis fuertes ROB leerá varias páginas del documento.
9. proposed_actions contiene pasos, nunca conclusiones.
10. Devuelve exclusivamente JSON válido conforme al esquema solicitado.
11. No expliques tu razonamiento y no hagas análisis paso a paso. /no_think
"""


_NAME_LINE = re.compile(
    r"^[A-ZÁÉÍÓÚÜÑ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ'’.-]+(?:\s+[A-ZÁÉÍÓÚÜÑ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ'’.-]+){1,4}$"
)
_YEAR = re.compile(r"\b(1[3-9]\d{2}|20\d{2})\b")
_UI_LINES = {
    "perfil", "editar", "agregar", "más", "biografía", "fotos y videos", "fotos y vídeos",
    "eventos", "añadir hecho", "investigar a esta persona", "cargar fotos y vídeos de manuel",
}


def _clean_lines(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]


def _candidate_name_lines(lines: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for line in lines:
        lowered = line.casefold().strip(" :")
        if lowered in _UI_LINES or line.endswith(":") or "," in line or "(" in line or ")" in line:
            continue
        if not _NAME_LINE.fullmatch(line):
            continue
        key = line.casefold()
        if key not in seen:
            seen.add(key)
            output.append(line)
    return output


def fallback_interpretation(raw_request: str, *, reason: str | None = None) -> dict[str, Any]:
    """Deterministic safety net so a slow local LLM never blocks research.

    It deliberately extracts only explicit surface information. The deep local model may refine
    it later, but this provisional interpretation is sufficient to start evidence retrieval.
    """
    text = str(raw_request)
    lines = _clean_lines(text)
    names = _candidate_name_lines(lines)
    target = names[0] if names else None
    relatives = names[1:20] if len(names) > 1 else []

    years = [int(value) for value in _YEAR.findall(text)]
    years = sorted(set(years))
    year_from = years[0] if years else None
    year_to = years[-1] if years else None

    places: list[str] = []
    for line in lines:
        if "," not in line:
            continue
        lowered = line.casefold()
        if any(word in lowered for word in ("españa", "spain", "galicia", "coruña", "lugo", "arzúa", "arzua", "pontevedra", "ourense")):
            if line not in places:
                places.append(line)
        if len(places) >= 10:
            break

    lower = text.casefold()
    family_scope: list[str] = []
    for label, needles in (
        ("padres", ("padre", "madre", "padres")),
        ("hijos", ("hijo", "hija", "hijos", "hijas")),
        ("cónyuge", ("matrimonio", "esposo", "esposa", "cónyuge", "conyuge")),
        ("hermanos", ("hermano", "hermana", "hermanos", "hermanas")),
        ("abuelos", ("abuelo", "abuela", "abuelos", "abuelas")),
    ):
        if any(needle in lower for needle in needles):
            family_scope.append(label)

    uncertainties = [
        "Interpretación provisional obtenida por extracción local conservadora; debe contrastarse con documentos."
    ]
    if reason:
        uncertainties.append(f"La IA rápida no terminó a tiempo: {reason}")
    if not target:
        uncertainties.append("No se pudo aislar con seguridad un nombre objetivo principal.")

    target_candidates = []
    if target:
        target_candidates.append(
            {
                "value": target,
                "confidence": 0.98,
                "reason": "Primera línea explícita del texto con forma de nombre personal completo.",
            }
        )

    return {
        "raw_request": text,
        "intent": "investigate_person" if target else "genealogical_research",
        "objectives": [
            "buscar evidencia documental sobre la persona objetivo",
            "identificar y contrastar relaciones familiares explícitas",
            "recuperar variantes y lecturas OCR degradadas sin sustituir el original",
        ],
        "target_candidates": target_candidates,
        "people_mentions": relatives,
        "places": places,
        "chronology": {
            "year_from": year_from,
            "year_to": year_to,
            "around": None,
            "confidence": 0.75 if years else 0.0,
        },
        "family_scope": family_scope,
        "ocr_tolerant": True,
        "multipage_required": True,
        "uncertainties": uncertainties,
        "proposed_actions": [
            "buscar primero el nombre completo y sus componentes",
            "cruzar persona con lugares, fechas y familiares mencionados",
            "rankear candidatos por evidencia antes de abrir contexto profundo",
            "leer varias páginas del mismo documento antes de formular hipótesis fuertes",
        ],
    }


async def interpret_research_request(provider: AIProvider, raw_request: str) -> dict[str, Any]:
    text = str(raw_request)
    if not text.strip():
        raise ValueError("Research request cannot be empty")
    value = await chat_json(
        provider,
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text + "\n\n/no_think"},
        ],
        temperature=0.1,
        max_tokens=750,
        json_schema=INTERPRETATION_SCHEMA,
    )
    value["raw_request"] = text
    value["multipage_required"] = True
    return value
