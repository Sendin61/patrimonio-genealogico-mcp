from __future__ import annotations

import asyncio

import pytest

from rob.lab.document_context import DocumentContextBuilder
from rob.lab.models import OCRResolution, OCRStatus, PageContext


def test_document_context_expands_until_detected_boundaries() -> None:
    pages = {
        number: PageContext(image_number=number, raw_text=f"page {number}")
        for number in range(6, 15)
    }

    async def load(number: int) -> PageContext | None:
        return pages.get(number)

    async def continuity(left: PageContext, right: PageContext, direction: str):
        same_document = 8 <= left.image_number <= 12 and 8 <= right.image_number <= 12
        return same_document, f"{direction}: {left.image_number}->{right.image_number}={same_document}"

    builder = DocumentContextBuilder(
        load,
        continuity,
        initial_radius=1,
        max_extra_pages_each_side=10,
    )
    context = asyncio.run(builder.build(10))

    assert context.center_image == 10
    assert context.estimated_start_image == 8
    assert context.estimated_end_image == 12
    assert [page.image_number for page in context.pages] == [8, 9, 10, 11, 12]
    assert "[[IMG 10]]" in context.combined_ocr


def test_ocr_resolution_never_discards_raw_ocr() -> None:
    resolution = OCRResolution(
        raw_ocr="Bea",
        resolved_text="Varela",
        confidence=0.91,
        resolution_evidence=["family context"],
    )

    assert resolution.raw_ocr == "Bea"
    assert resolution.resolved_text == "Varela"
    assert resolution.status is OCRStatus.INFERRED


def test_ocr_confidence_is_bounded() -> None:
    with pytest.raises(ValueError):
        OCRResolution(raw_ocr="Bea", resolved_text="Varela", confidence=1.2)
