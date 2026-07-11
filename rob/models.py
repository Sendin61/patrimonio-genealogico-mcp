from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SourceStatus(StrEnum):
    REGISTERED = "registered"
    DEVELOPMENT = "development"
    FUNCTIONAL = "functional"
    VERIFIED = "verified"
    DISABLED = "disabled"


@dataclass(slots=True)
class GenealogyQuery:
    name: str
    variants: list[str] = field(default_factory=list)
    places: list[str] = field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None
    spouse: str | None = None
    profession: str | None = None
    extra_terms: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SearchResult:
    source_id: str
    source_name: str
    territory: str
    title: str
    url: str
    matched_text: str | None = None
    date: str | None = None
    place: str | None = None
    page: str | None = None
    document_type: str | None = None
    archive_reference: str | None = None
    score: float = 0.0
    score_reasons: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
