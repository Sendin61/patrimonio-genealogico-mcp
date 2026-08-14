from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _norm(value: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", _clean(value).casefold())
        if not unicodedata.combining(ch)
    )


def _quote(value: str) -> str:
    return '"' + _clean(value).replace('"', '') + '"'


def _tokens(value: str) -> list[str]:
    return re.findall(r"[\wÀ-ÿ'-]+", _clean(value), flags=re.UNICODE)


def _required_terms(values: list[str]) -> str:
    terms: list[str] = []
    for value in values:
        terms.extend(_tokens(value))
    return " ".join("+" + token for token in terms if token)


def _single_char_ocr_variants(value: str) -> list[str]:
    """Small, high-value wildcard set for FamilySearch full-text.

    Captured FamilySearch behaviour shows '?' can recover one-character OCR variation.
    We keep this conservative; learned corpus-specific variants are added later.
    """
    token = _clean(value)
    if len(token) < 6 or " " in token:
        return []
    positions = sorted({len(token) // 3, len(token) // 2, (2 * len(token)) // 3})
    return [token[:p] + "?" + token[p + 1 :] for p in positions if 0 < p < len(token) - 1]


@dataclass(frozen=True, slots=True)
class SearchAction:
    kind: str
    phase: str
    priority: int
    reason: str
    query: str | None = None
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ResearchPlan:
    actions: list[SearchAction]
    ocr_tolerant: bool = True
    multipage_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "ocr_tolerant": self.ocr_tolerant,
            "multipage_required": self.multipage_required,
            "actions": [action.to_dict() for action in self.actions],
        }


def build_research_plan(interpretation: dict[str, Any]) -> ResearchPlan:
    candidates = []
    for item in interpretation.get("target_candidates", []) or []:
        if isinstance(item, dict):
            value = _clean(item.get("value", ""))
        else:
            value = _clean(item)
        if value and _norm(value) not in {_norm(x) for x in candidates}:
            candidates.append(value)

    people = [_clean(x) for x in interpretation.get("people_mentions", []) or [] if _clean(x)]
    places = [_clean(x) for x in interpretation.get("places", []) or [] if _clean(x)]
    chronology = interpretation.get("chronology") if isinstance(interpretation.get("chronology"), dict) else {}
    around = chronology.get("around")
    year_from = chronology.get("year_from")
    year_to = chronology.get("year_to")
    ocr_tolerant = bool(interpretation.get("ocr_tolerant", True))

    actions: list[SearchAction] = []
    seen: set[tuple[str, str]] = set()

    def add(query: str, *, phase: str, priority: int, reason: str) -> None:
        query = _clean(query)
        key = ("fulltext_search", _norm(query))
        if not query or key in seen:
            return
        seen.add(key)
        actions.append(SearchAction("fulltext_search", phase, priority, reason, query=query))

    for candidate in candidates:
        add(_quote(candidate), phase="01_identity_phrase", priority=100, reason="Forma completa candidata interpretada del objetivo.")
        required = _required_terms([candidate])
        add(required, phase="02_identity_required", priority=96, reason="Todos los componentes del nombre requeridos; tolera separación en el OCR.")

        for place in places:
            add(
                f"{required} +{place}",
                phase="03_identity_place",
                priority=93,
                reason="Cruce entre persona candidata y lugar aportado por el expediente.",
            )

        years = [year for year in (around, year_from, year_to) if isinstance(year, int)]
        for year in dict.fromkeys(years):
            add(
                f"{required} +{year}",
                phase="04_identity_chronology",
                priority=82,
                reason="Año explícito o aproximado del objetivo; se usa como pista, no como hecho.",
            )

        for other in people:
            if _norm(other) == _norm(candidate):
                continue
            add(
                f"{required} {_required_terms([other])}",
                phase="05_family_cooccurrence",
                priority=90,
                reason="Coaparición del objetivo y otra persona mencionada en la petición.",
            )

        if ocr_tolerant:
            name_tokens = _tokens(candidate)
            for index, token in enumerate(name_tokens):
                for wildcard in _single_char_ocr_variants(token):
                    variant_tokens = list(name_tokens)
                    variant_tokens[index] = wildcard
                    add(
                        " ".join("+" + x for x in variant_tokens),
                        phase="06_ocr_wildcard",
                        priority=68,
                        reason=f"Variante de un carácter para detectar OCR degradado de '{token}'.",
                    )

    # Surname/place and family searches recover records where the given name is badly OCR'd.
    for candidate in candidates:
        tokens = _tokens(candidate)
        if len(tokens) >= 2:
            for width in (2, 1):
                tail = tokens[-width:]
                if not tail:
                    continue
                for place in places:
                    add(
                        f"{_required_terms(tail)} +{place}",
                        phase="07_surname_place_recall",
                        priority=72 if width == 2 else 58,
                        reason="Recuperación por apellido(s) y geografía cuando el nombre de pila falla en OCR.",
                    )

    actions.sort(key=lambda item: (-item.priority, item.phase, item.query or ""))
    return ResearchPlan(
        actions=actions,
        ocr_tolerant=ocr_tolerant,
        multipage_required=True,
    )
