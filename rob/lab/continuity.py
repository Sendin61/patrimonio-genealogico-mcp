from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .ai.base import AIProvider, chat_json
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
acto/documento genealógico. El OCR puede estar muy degradado. Mira continuidad sintáctica,
protagonistas, fechas, fórmulas notariales/parroquiales, cierres y comienzos de nuevos actos.
No exijas que se repita el mismo apellido. Si hay duda real, indica confianza baja.
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

        content = (
            f"DIRECCION: {direction}\n"
            f"PAGINA_IZQUIERDA: {left.image_number}\n"
            f"FINAL_IZQUIERDA:\n{_edge(left.raw_text, start=False)}\n\n"
            f"PAGINA_DERECHA: {right.image_number}\n"
            f"INICIO_DERECHA:\n{_edge(right.raw_text, start=True)}"
        )
        value = await chat_json(
            self.provider,
            [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": content},
            ],
            temperature=0.0,
            max_tokens=380,
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
