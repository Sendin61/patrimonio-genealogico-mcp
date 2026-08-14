from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class OCRStatus(StrEnum):
    OBSERVED = "observed"
    INFERRED = "inferred"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


@dataclass(slots=True)
class OCRResolution:
    raw_ocr: str
    resolved_text: str | None = None
    confidence: float | None = None
    status: OCRStatus = OCRStatus.OBSERVED
    resolution_evidence: list[str] = field(default_factory=list)
    image_region: tuple[int, int, int, int] | None = None
    source_token_id: str | None = None

    def __post_init__(self) -> None:
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.status is OCRStatus.OBSERVED and self.resolved_text:
            self.status = OCRStatus.INFERRED

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value


@dataclass(slots=True)
class PageContext:
    image_number: int
    raw_text: str
    ark: str | None = None
    dgs: str | None = None
    structured_tokens: list[dict[str, Any]] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    continuation_backward: bool | None = None
    continuation_forward: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_text(self) -> bool:
        return bool(self.raw_text.strip())


@dataclass(slots=True)
class DocumentContext:
    center_image: int
    pages: list[PageContext]
    estimated_start_image: int | None = None
    estimated_end_image: int | None = None
    boundary_confidence: float | None = None
    reasons: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.pages.sort(key=lambda page: page.image_number)
        if self.estimated_start_image is None and self.pages:
            self.estimated_start_image = self.pages[0].image_number
        if self.estimated_end_image is None and self.pages:
            self.estimated_end_image = self.pages[-1].image_number

    @property
    def combined_ocr(self) -> str:
        return "\n\n".join(
            f"[[IMG {page.image_number}]]\n{page.raw_text.strip()}"
            for page in self.pages
            if page.raw_text.strip()
        )

    def page(self, image_number: int) -> PageContext | None:
        return next(
            (page for page in self.pages if page.image_number == image_number),
            None,
        )
