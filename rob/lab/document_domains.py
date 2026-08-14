from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip()


def _contains(text: str, phrases: Iterable[str]) -> list[str]:
    normalized = _norm(text)
    found: list[str] = []
    for phrase in phrases:
        if _norm(phrase) in normalized:
            found.append(phrase)
    return found


@dataclass(frozen=True, slots=True)
class HistoricalDocumentProfile:
    key: str
    label: str
    regions: tuple[str, ...]
    languages: tuple[str, ...]
    record_types: tuple[str, ...]
    start_markers: tuple[str, ...]
    continuation_markers: tuple[str, ...]
    end_markers: tuple[str, ...]
    relationship_markers: tuple[str, ...]
    role_markers: tuple[str, ...]
    notes: tuple[str, ...]

    def score(self, text: str) -> tuple[int, list[str]]:
        reasons: list[str] = []
        score = 0
        for label, phrases, weight in (
            ("tipo", self.record_types, 5),
            ("inicio", self.start_markers, 4),
            ("continuidad", self.continuation_markers, 2),
            ("cierre", self.end_markers, 3),
            ("parentesco", self.relationship_markers, 3),
            ("roles", self.role_markers, 2),
        ):
            matches = _contains(text, phrases)
            if matches:
                score += min(15, len(matches) * weight)
                reasons.append(f"{label}: {', '.join(matches[:8])}")
        return score, reasons


SPAIN_NOTARIAL_SUCCESSORAL = HistoricalDocumentProfile(
    key="spain_notarial_successoral",
    label="España · notarial/sucesorio/protocolario",
    regions=("ES", "PT", "iberoamerica"),
    languages=("es", "gl", "ca", "pt", "la"),
    record_types=(
        "testamento", "codicilo", "protocolo", "escritura", "particion", "partija",
        "inventario", "hijuela", "poder", "venta", "compraventa", "obligacion",
        "carta de pago", "capitulaciones", "dote", "foro", "arrendamiento",
        "donacion", "cesion", "renuncia", "adjudicacion", "herencia",
    ),
    start_markers=(
        "en la villa de", "en la ciudad de", "en el lugar de", "ante mi", "ante mí",
        "comparecio", "compareció", "parecio", "pareció", "otorga", "otorgo", "otorgó",
        "sepan cuantos", "en nombre de dios", "hallandome enfermo", "hallándome enfermo",
        "estando en mi sano juicio", "digo y declaro",
    ),
    continuation_markers=(
        "item", "tambien", "también", "declaro", "mando", "lego", "dispongo",
        "bienes", "fincas", "herederos", "heredero", "heredera", "legitima", "legítima",
        "quinto", "tercio", "mejora", "usufructo", "deudas", "cargas", "particion",
        "partición", "inventario", "adjudico", "adjudica", "corresponde",
    ),
    end_markers=(
        "asi lo otorgo", "así lo otorgó", "firma", "firmo", "firmó", "no firma",
        "testigos", "fueron testigos", "ante mi", "ante mí", "doy fe", "signo",
        "rubrica", "rúbrica", "queda protocolado", "es copia",
    ),
    relationship_markers=(
        "hijo legitimo", "hijo legítimo", "hija legitima", "hija legítima", "hijo natural",
        "hija natural", "mi hijo", "mi hija", "mis hijos", "mi mujer", "mi marido",
        "mi esposa", "mi esposo", "viudo de", "viuda de", "nieto", "nieta", "hermano",
        "hermana", "sobrino", "sobrina", "yerno", "nuera", "padre", "madre",
        "abuelos", "consorte", "conyuge", "cónyuge",
    ),
    role_markers=(
        "testador", "testadora", "otorgante", "heredero", "heredera", "legatario",
        "legataria", "albacea", "contador", "partidor", "tutor", "curador", "apoderado",
        "poderdante", "comprador", "vendedor", "fiador", "testigo", "notario", "escribano",
    ),
    notes=(
        "La estructura puede extenderse muchas páginas y cambiar de asunto sin repetir el nombre objetivo.",
        "El parentesco y los roles jurídicos pesan más que la mera proximidad ortográfica de un apellido.",
        "Las fórmulas notariales y los cierres ayudan a detectar límites de documento incluso con OCR degradado.",
    ),
)

SPAIN_LITIGATION = HistoricalDocumentProfile(
    key="spain_litigation",
    label="España · judicial/pleitos/expedientes",
    regions=("ES", "PT", "iberoamerica"),
    languages=("es", "gl", "ca", "pt", "la"),
    record_types=(
        "pleito", "autos", "expediente", "demanda", "querella", "ejecutoria", "sentencia",
        "informacion", "información", "probanzas", "diligencias", "recurso", "apelacion", "apelación",
    ),
    start_markers=(
        "en los autos", "ante el juez", "comparece", "comparecio", "compareció", "digo",
        "por presentado", "se presenta", "a instancia de", "en nombre y representacion",
        "en nombre y representación",
    ),
    continuation_markers=(
        "resultando", "considerando", "declara", "manifiesta", "expone", "dice", "preguntado",
        "respondio", "respondió", "testigo", "declaracion", "declaración", "folio", "pieza",
        "diligencia", "providencia", "notificacion", "notificación",
    ),
    end_markers=(
        "sentencia", "fallo", "mandamos", "firmado", "notifiquese", "notifíquese", "doy fe",
        "ante mi", "ante mí", "archivese", "archívese",
    ),
    relationship_markers=SPAIN_NOTARIAL_SUCCESSORAL.relationship_markers,
    role_markers=(
        "demandante", "demandado", "actor", "reo", "querellante", "querellado", "testigo",
        "juez", "fiscal", "procurador", "abogado", "escribano", "secretario", "perito",
    ),
    notes=(
        "Los expedientes pueden contener documentos insertos, testimonios y copias de actos anteriores.",
        "La continuidad puede ser temática o procesal aunque cambien las personas mencionadas de una página a otra.",
    ),
)

SPAIN_PARISH_CIVIL = HistoricalDocumentProfile(
    key="spain_parish_civil",
    label="España · parroquial/registro civil/padrones",
    regions=("ES", "PT", "iberoamerica"),
    languages=("es", "gl", "ca", "pt", "la"),
    record_types=(
        "bautismo", "bautizado", "matrimonio", "casamiento", "defuncion", "defunción", "entierro",
        "nacimiento", "registro civil", "padron", "padrón", "vecindario", "censo",
    ),
    start_markers=(
        "en esta parroquia", "en esta iglesia", "en el dia", "en el día", "a los", "nacio", "nació",
        "bautice", "bauticé", "contrajeron matrimonio", "fallecio", "falleció", "comparece",
    ),
    continuation_markers=(
        "hijo de", "hija de", "natural de", "vecino de", "vecina de", "abuelos paternos",
        "abuelos maternos", "padrinos", "testigos", "legitimo", "legítimo", "conyuges", "cónyuges",
    ),
    end_markers=(
        "padrinos", "testigos", "firma", "firmo", "firmó", "doy fe", "cura", "parroco", "párroco",
        "encargado del registro", "juez municipal",
    ),
    relationship_markers=SPAIN_NOTARIAL_SUCCESSORAL.relationship_markers,
    role_markers=(
        "bautizado", "bautizada", "contrayente", "difunto", "difunta", "padre", "madre",
        "padrino", "madrina", "testigo", "cura", "parroco", "párroco", "juez municipal",
    ),
    notes=(
        "En registros seriados, el límite de una partida puede estar muy cerca de la siguiente y compartir fórmulas idénticas.",
    ),
)

GENERIC_HISTORICAL = HistoricalDocumentProfile(
    key="generic_historical",
    label="Histórico genérico internacional",
    regions=("world",),
    languages=("multi",),
    record_types=(),
    start_markers=(),
    continuation_markers=(),
    end_markers=(),
    relationship_markers=("son of", "daughter of", "wife", "husband", "widow", "widower", "father", "mother"),
    role_markers=(),
    notes=(
        "Usar estructura, repetición de actores, fechas, lugares y continuidad sintáctica sin imponer fórmulas españolas.",
    ),
)

PROFILES = (
    SPAIN_NOTARIAL_SUCCESSORAL,
    SPAIN_LITIGATION,
    SPAIN_PARISH_CIVIL,
    GENERIC_HISTORICAL,
)


def detect_document_profiles(text: str, *, limit: int = 3) -> list[dict[str, object]]:
    ranked: list[tuple[int, HistoricalDocumentProfile, list[str]]] = []
    for profile in PROFILES:
        score, reasons = profile.score(text)
        if score or profile.key == "generic_historical":
            ranked.append((score, profile, reasons))
    ranked.sort(key=lambda item: (-item[0], item[1].key))
    output: list[dict[str, object]] = []
    for score, profile, reasons in ranked[: max(1, limit)]:
        output.append(
            {
                "key": profile.key,
                "label": profile.label,
                "score": score,
                "reasons": reasons,
                "notes": list(profile.notes),
                "start_markers": list(profile.start_markers[:20]),
                "continuation_markers": list(profile.continuation_markers[:20]),
                "end_markers": list(profile.end_markers[:20]),
                "relationship_markers": list(profile.relationship_markers[:24]),
                "role_markers": list(profile.role_markers[:24]),
            }
        )
    return output
