from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from rob.models import GenealogyQuery


@dataclass(slots=True)
class InvestigationTarget:
    """User-supplied target data, kept separate from documentary evidence."""

    name: str
    variants: list[str] = field(default_factory=list)
    places: list[str] = field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None
    spouse: str | None = None
    profession: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "InvestigationTarget":
        return cls(
            name=str(value.get("name") or ""),
            variants=[str(item) for item in value.get("variants", [])],
            places=[str(item) for item in value.get("places", [])],
            year_from=value.get("year_from"),
            year_to=value.get("year_to"),
            spouse=str(value.get("spouse") or "") or None,
            profession=str(value.get("profession") or "") or None,
        )

    def to_genealogy_query(self) -> GenealogyQuery:
        return GenealogyQuery(
            name=self.name,
            variants=list(self.variants),
            places=list(self.places),
            year_from=self.year_from,
            year_to=self.year_to,
            spouse=self.spouse,
            profession=self.profession,
        )


@dataclass(frozen=True, slots=True)
class SourceCapabilities:
    create_investigation: bool = True
    process_batches: bool = True
    report: bool = True
    read_source: bool = True

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)
