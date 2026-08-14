from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from .ai.base import AIProvider, chat_json
from .models import DocumentContext, OCRResolution, OCRStatus, PageContext


def _norm(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", str(value).casefold())
        if not unicodedata.combining(character)
    )


def _windows(text: str, terms: list[str], *, radius: int = 1100) -> list[str]:
    normalised = _norm(text)
    spans: list[tuple[int, int]] = []
    for term in terms:
        needle = _norm(term).strip()
        if not needle:
            continue
        start = 0
        while True:
            found = normalised.find(needle, start)
            if found < 0:
                break
            spans.append((max(0, found - radius), min(len(text), found + len(needle) + radius)))
            start = found + max(1, len(needle))
    if not spans:
        return []
    spans.sort()
    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1] + 200:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [text[start:end].strip() for start, end in merged[:4] if text[start:end].strip()]


def multipage_excerpt(
    context: DocumentContext,
    *,
    focus_terms: list[str],
    maximum_characters: int = 50000,
) -> str:
    """Represent every page while concentrating raw OCR around useful terms.

    A large document is not silently reduced to only the matching page. Every loaded page
    contributes at least its beginning/end, while pages containing focus terms contribute
    wider literal OCR windows. This keeps multi-page evidence visible within a local model's
    finite context window.
    """

    sections: list[str] = []
    per_page_soft = max(1400, maximum_characters // max(1, len(context.pages)))
    for page in context.pages:
        raw = page.raw_text.strip()
        if not raw:
            sections.append(f"[[IMG {page.image_number}]]\n[SIN OCR]")
            continue

        hits = _windows(raw, focus_terms, radius=max(650, per_page_soft // 3))
        if hits:
            body = "\n…\n".join(hits)
        elif len(raw) <= per_page_soft:
            body = raw
        else:
            edge = max(450, per_page_soft // 2)
            body = raw[:edge] + "\n… [CENTRO OMITIDO POR CONTEXTO] …\n" + raw[-edge:]
        sections.append(f"[[IMG {page.image_number}]]\n{body}")

    combined = "\n\n".join(sections)
    if len(combined) <= maximum_characters:
        return combined

    # Second pass: shrink evenly but never drop an entire page.
    allowance = max(700, maximum_characters // max(1, len(sections)))
    compact: list[str] = []
    for section in sections:
        if len(section) <= allowance:
            compact.append(section)
        else:
            head = allowance // 2
            compact.append(section[:head] + "\n…\n" + section[-head:])
    return "\n\n".join(compact)[:maximum_characters]


RESOLUTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidate": {"type": ["string", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "alternatives": {"type": "array", "items": {"type": "string"}},
        "needs_visual_check": {"type": "boolean"},
        "reasoning_summary": {"type": "string"},
    },
    "required": [
        "candidate",
        "confidence",
        "evidence",
        "alternatives",
        "needs_visual_check",
        "reasoning_summary",
    ],
}


SYSTEM_PROMPT = """Eres el Contextual OCR Resolver de ROB Genealogy Lab.
Analizas una lectura OCR dudosa dentro de un documento genealógico HISTÓRICO MULTIPÁGINA.

Reglas epistemológicas obligatorias:
- RAW_OCR es evidencia observada y nunca se modifica.
- candidate es solo una lectura propuesta.
- Una gran distancia ortográfica NO impide una propuesta si el contexto documental,
  genealógico y repetitivo es fuerte. Por ejemplo, 'Bea' podría resultar ser 'Varela'
  si otras páginas, parentescos, nombres y fórmulas del mismo documento lo sostienen.
- Tampoco fuerces una corrección: si la evidencia no basta, candidate=null o confianza baja.
- Usa varias páginas, no únicamente la página central.
- Distingue evidencia literal del documento de conocimiento previo aportado por el expediente.
- No inventes letras que no puedas justificar.
- Si la imagen/recorte original sería decisiva, needs_visual_check=true.
- Devuelve exclusivamente JSON conforme al esquema.
"""


@dataclass(slots=True)
class ContextualOCRResolver:
    provider: AIProvider

    async def resolve(
        self,
        *,
        raw_ocr: str,
        document: DocumentContext,
        page_number: int | None = None,
        known_people: list[str] | None = None,
        known_places: list[str] | None = None,
        research_notes: list[str] | None = None,
        image_region: tuple[int, int, int, int] | None = None,
    ) -> OCRResolution:
        known_people = [str(x) for x in (known_people or []) if str(x).strip()]
        known_places = [str(x) for x in (known_places or []) if str(x).strip()]
        research_notes = [str(x) for x in (research_notes or []) if str(x).strip()]
        focus_terms = [raw_ocr, *known_people, *known_places]
        excerpt = multipage_excerpt(document, focus_terms=focus_terms)

        user_content = (
            f"RAW_OCR: {raw_ocr}\n"
            f"PAGINA_DUDOSA: {page_number if page_number is not None else 'desconocida'}\n"
            f"LIMITE_DOCUMENTO_ESTIMADO: {document.estimated_start_image}-{document.estimated_end_image}\n"
            f"PERSONAS_CONOCIDAS_DEL_EXPEDIENTE: {known_people}\n"
            f"LUGARES_CONOCIDOS: {known_places}\n"
            f"NOTAS_DE_INVESTIGACION_NO_CONFIRMADAS: {research_notes}\n\n"
            "OCR MULTIPAGINA (literal, con posibles errores):\n"
            f"{excerpt}"
        )
        value = await chat_json(
            self.provider,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.0,
            max_tokens=1800,
            json_schema=RESOLUTION_SCHEMA,
        )

        candidate = value.get("candidate")
        candidate = str(candidate).strip() if candidate is not None else None
        if not candidate:
            candidate = None
        confidence = float(value.get("confidence", 0.0))
        evidence = [str(item) for item in value.get("evidence", [])]
        summary = str(value.get("reasoning_summary") or "").strip()
        if summary:
            evidence.append(summary)
        if value.get("needs_visual_check"):
            evidence.append("Comprobación visual del recorte original recomendada.")
        alternatives = [str(item) for item in value.get("alternatives", [])]
        if alternatives:
            evidence.append("Alternativas consideradas: " + "; ".join(alternatives[:8]))

        return OCRResolution(
            raw_ocr=raw_ocr,
            resolved_text=candidate,
            confidence=confidence,
            status=OCRStatus.INFERRED if candidate else OCRStatus.OBSERVED,
            resolution_evidence=evidence,
            image_region=image_region,
        )
