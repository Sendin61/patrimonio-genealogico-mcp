from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .ai.base import AIProvider, chat_json
from .document_domains import detect_document_profiles
from .models import PageContext


SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "same_document": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
    "required": ["same_document", "confidence", "reason"],
}


SYSTEM = """Decide si dos páginas OCR históricas contiguas parecen pertenecer al mismo
acto/documento genealógico.

Dominio prioritario: documentación histórica española e ibérica, especialmente protocolos
notariales, sucesiones, testamentos, inventarios, particiones/partijas, poderes, compraventas,
pleitos, expedientes, registros parroquiales/civiles y padrones. También debes funcionar con
documentación internacional cuando las fórmulas españolas no sean aplicables.

Reglas:
- El OCR puede estar muy degradado, sin puntuación fiable y con palabras severamente truncadas.
- No compares páginas como textos modernos. Busca continuidad DOCUMENTAL: acto jurídico,
  otorgante, causante, herederos, bienes, declaraciones, testigos, fechas, lugar, escribano/notario,
  fórmulas de apertura/cierre, referencias anafóricas y continuación sintáctica.
- Un documento puede cambiar de persona o bien descrito y seguir siendo el mismo acto.
- Un nuevo acto puede reutilizar exactamente las mismas fórmulas notariales y apellidos.
- Un cierre fuerte al final de la izquierda + una apertura fuerte al inicio de la derecha es una
  señal importante de límite, pero no absoluta si hay copia, diligencia o documento inserto.
- En registros seriados parroquiales/civiles, varias partidas consecutivas pueden usar fórmulas
  casi idénticas; prioriza cambio de fecha/protagonista y señales de nueva partida.
- No exijas que se repita el mismo apellido.
- Si hay duda real, devuelve confianza baja.
Devuelve exclusivamente JSON conforme al esquema."""


def _edge(text: str, *, start: bool, maximum: int = 4500) -> str:
    value = str(text or "").strip()
    if len(value) <= maximum:
        return value
    return value[:maximum] if start else value[-maximum:]


@dataclass(slots=True)
class AIContinuityDecider:
    provider: AIProvider
    threshold: float = 0.58

    async def __call__(
        self,
        left: PageContext,
        right: PageContext,
        direction: str,
    ) -> tuple[bool, str]:
        if not left.raw_text.strip() or not right.raw_text.strip():
            return False, f"{direction}: OCR vacío en una de las páginas"

        left_edge = _edge(left.raw_text, start=False)
        right_edge = _edge(right.raw_text, start=True)
        structural = detect_document_profiles(left_edge + "\n" + right_edge, limit=2)

        # Strong deterministic boundary shortcut: a likely closing formula on the left and
        # likely opening formula on the right.  This saves a local-LLM call in common notarial
        # and serial-register boundaries, but only when both sides are independently supported.
        best = structural[0] if structural else {}
        end_markers = [str(x).casefold() for x in best.get("end_markers", [])]
        start_markers = [str(x).casefold() for x in best.get("start_markers", [])]
        left_norm = left_edge.casefold()
        right_norm = right_edge.casefold()
        left_end_hits = [m for m in end_markers if m and m in left_norm]
        right_start_hits = [m for m in start_markers if m and m in right_norm]
        if left_end_hits and right_start_hits:
            return False, (
                f"{direction} {left.image_number}->{right.image_number}: límite estructural probable — "
                f"cierre izquierda ({', '.join(left_end_hits[:3])}) + apertura derecha "
                f"({', '.join(right_start_hits[:3])})"
            )

        content = (
            f"DIRECCION: {direction}\n"
            f"PERFILES_DOCUMENTALES_PROBABLES: {structural}\n"
            f"PAGINA_IZQUIERDA: {left.image_number}\n"
            f"FINAL_IZQUIERDA:\n{left_edge}\n\n"
            f"PAGINA_DERECHA: {right.image_number}\n"
            f"INICIO_DERECHA:\n{right_edge}"
        )
        value = await chat_json(
            self.provider,
            [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": content},
            ],
            temperature=0.0,
            max_tokens=420,
            json_schema=SCHEMA,
        )
        same = bool(value.get("same_document"))
        confidence = float(value.get("confidence", 0.0))
        reason = str(value.get("reason") or "sin explicación")
        continues = same and confidence >= self.threshold
        return continues, (
            f"{direction} {left.image_number}->{right.image_number}: "
            f"{'continúa' if continues else 'límite probable'} "
            f"(confianza {confidence:.2f}) — {reason}"
        )
