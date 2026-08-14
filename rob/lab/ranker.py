from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any


KINSHIP = (
    "hijo", "hija", "padre", "madre", "abuelo", "abuela", "hermano", "hermana",
    "marido", "mujer", "esposo", "esposa", "viudo", "viuda", "suegro", "suegra",
    "nieto", "nieta", "heredero", "heredera",
)
FORMULAS = (
    "hijo legítimo de", "hija legítima de", "hijo legitimo de", "hija legitima de",
    "hijo natural de", "hija natural de", "mujer de", "marido de", "viuda de",
    "viudo de", "natural de", "vecino de", "vecina de", "sus padres", "sus abuelos",
)
DOCUMENT_TERMS = (
    "testamento", "codicilo", "inventario", "partija", "partición", "particion",
    "probanza", "pleito", "dote", "capellanía", "capellania", "mayorazgo",
    "bautismo", "matrimonio", "defunción", "defuncion", "notarial", "protocolo",
)


def norm(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or "").casefold())
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", plain).strip()


def contains(text: str, value: str) -> bool:
    needle = norm(value)
    return bool(needle and needle in text)


@dataclass(frozen=True, slots=True)
class ScoreReason:
    points: int
    reason: str
    evidence: str = ""


@dataclass(slots=True)
class RankedCandidate:
    source_key: str
    score: int
    entry: dict[str, Any]
    reasons: list[ScoreReason] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_key": self.source_key,
            "score": self.score,
            "reasons": [asdict(reason) for reason in self.reasons],
            "entry": self.entry,
        }


def score_fulltext_entry(entry: dict[str, Any], interpretation: dict[str, Any]) -> RankedCandidate:
    content = entry.get("content") if isinstance(entry.get("content"), dict) else {}
    text_raw = "\n".join(
        str(x or "")
        for x in (
            content.get("textDocument"),
            content.get("title"),
            content.get("recordPlace"),
            content.get("recordDate"),
            entry.get("collectionTitle"),
        )
    )
    text = norm(text_raw)
    highlights = [str(x) for x in (content.get("highlightTexts") or [])]
    entities = [
        str(item.get("value") or "")
        for item in (content.get("entities") or [])
        if isinstance(item, dict)
    ]

    targets = []
    for item in interpretation.get("target_candidates", []) or []:
        value = item.get("value") if isinstance(item, dict) else item
        if str(value or "").strip():
            targets.append(str(value).strip())
    people = [str(x) for x in interpretation.get("people_mentions", []) or []]
    places = [str(x) for x in interpretation.get("places", []) or []]
    chronology = interpretation.get("chronology") if isinstance(interpretation.get("chronology"), dict) else {}

    reasons: list[ScoreReason] = []
    def add(points: int, reason: str, evidence: str = "") -> None:
        if points:
            reasons.append(ScoreReason(points, reason, evidence))

    exact_targets = [value for value in targets if contains(text, value)]
    if exact_targets:
        add(44 + min(24, 8 * (len(exact_targets) - 1)), "Nombre objetivo presente en el OCR/documento.", "; ".join(exact_targets[:4]))

    highlighted_targets = [
        value for value in targets
        if any(norm(value) in norm(highlight) or norm(highlight) in norm(value) for highlight in highlights if highlight)
    ]
    if highlighted_targets:
        add(28, "FamilySearch resaltó una forma compatible con el objetivo.", "; ".join(highlights[:6]))

    entity_targets = [
        entity for entity in entities
        if any(norm(target) in norm(entity) or norm(entity) in norm(target) for target in targets if len(norm(entity)) >= 4)
    ]
    if entity_targets:
        add(12, "Entidad nominal detectada por FamilySearch compatible con el objetivo.", "; ".join(entity_targets[:6]))

    family_hits = [person for person in people if contains(text, person)]
    if family_hits:
        add(18 + min(18, 6 * (len(family_hits) - 1)), "Coaparición de personas asociadas al expediente.", "; ".join(family_hits[:6]))

    place_hits = [place for place in places if contains(text, place)]
    if place_hits:
        add(14 + min(10, 3 * (len(place_hits) - 1)), "Geografía compatible con la petición.", "; ".join(place_hits[:6]))

    formula_hits = [formula for formula in FORMULAS if formula in text]
    if formula_hits:
        add(12 + min(18, len(formula_hits) * 3), "Fórmulas genealógicas explícitas en el OCR.", "; ".join(formula_hits[:8]))
    kin_hits = [term for term in KINSHIP if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text)]
    if kin_hits:
        add(5 + min(12, len(set(kin_hits))), "Términos de parentesco en el documento.", "; ".join(sorted(set(kin_hits))[:8]))

    doc_hits = [term for term in DOCUMENT_TERMS if term in text]
    if doc_hits:
        add(5 + min(10, len(doc_hits) * 2), "Tipo documental potencialmente genealógico.", "; ".join(doc_hits[:6]))

    years = []
    for key in ("around", "year_from", "year_to"):
        value = chronology.get(key)
        if isinstance(value, int):
            years.append(value)
    found_years = [int(x) for x in re.findall(r"\b(1[3-9]\d{2}|20\d{2})\b", text)[:100]]
    if years and found_years:
        distance = min(abs(a - b) for a in years for b in found_years)
        if distance <= 3:
            add(12, "Cronología muy próxima a la indicada.", f"distancia mínima {distance} años")
        elif distance <= 15:
            add(6, "Cronología razonablemente próxima a la indicada.", f"distancia mínima {distance} años")
        elif distance >= 100:
            add(-12, "Cronología aparentemente muy alejada; puede ser mención retrospectiva u homónimo.", f"distancia mínima {distance} años")

    if not exact_targets and not family_hits and not place_hits:
        add(-18, "Coincidencia débil: sin objetivo, familia ni geografía reconocibles en el OCR recuperado.")

    score = max(0, sum(reason.points for reason in reasons))
    return RankedCandidate(
        source_key=str(entry.get("id") or entry.get("sourceUrl") or ""),
        score=score,
        entry=entry,
        reasons=sorted(reasons, key=lambda reason: abs(reason.points), reverse=True),
    )


def rank_fulltext_entries(entries: list[dict[str, Any]], interpretation: dict[str, Any]) -> list[RankedCandidate]:
    candidates = [score_fulltext_entry(entry, interpretation) for entry in entries]
    return sorted(candidates, key=lambda item: (-item.score, item.source_key))
