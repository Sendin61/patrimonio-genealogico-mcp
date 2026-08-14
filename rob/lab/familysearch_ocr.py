from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .familysearch_ids import parse_fs_image_id
from .models import PageContext


@dataclass(frozen=True, slots=True)
class StructuredOCRPage:
    image_ark: str
    apid: str | None
    fs_image_id: str | None
    group_id: str | None
    dgs: str | None
    dgs_image_number: int | None
    raw_text: str
    raw_json: dict[str, Any]

    def to_page_context(self) -> PageContext:
        number = self.dgs_image_number or 0
        return PageContext(
            image_number=number,
            ark=self.image_ark,
            raw_text=self.raw_text,
            structured_ocr=self.raw_json,
            metadata={
                "apid": self.apid,
                "fs_image_id": self.fs_image_id,
                "group_id": self.group_id,
                "dgs": self.dgs,
            },
        )


def _property_map(payload: dict[str, Any]) -> dict[str, str]:
    stuff = payload.get("stuff") if isinstance(payload.get("stuff"), dict) else {}
    metadata = stuff.get("metadata") if isinstance(stuff.get("metadata"), dict) else {}
    properties = metadata.get("properties") if isinstance(metadata.get("properties"), list) else []
    output: dict[str, str] = {}
    for item in properties:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        output[name] = str(item.get("value") or "")
    return output


def _line_text(tokens: list[Any]) -> str:
    words: list[str] = []
    for token in tokens:
        if not isinstance(token, dict):
            continue
        text = str(token.get("text") or "").strip()
        if text:
            words.append(text)
    return " ".join(words)


def structured_ocr_text(payload: dict[str, Any]) -> str:
    stuff = payload.get("stuff") if isinstance(payload.get("stuff"), dict) else {}
    regions = stuff.get("regions") if isinstance(stuff.get("regions"), list) else []
    paragraphs: list[str] = []
    for region in regions:
        if not isinstance(region, dict):
            continue
        # CRUFT tends to contain viewer marks rather than documentary text.
        if str(region.get("type") or "").upper() == "CRUFT":
            continue
        lines = region.get("lines") if isinstance(region.get("lines"), list) else []
        line_values = []
        for line in lines:
            if not isinstance(line, dict):
                continue
            value = _line_text(line.get("tokens") if isinstance(line.get("tokens"), list) else [])
            if value:
                line_values.append(value)
        if line_values:
            paragraphs.append("\n".join(line_values))
    return "\n\n".join(paragraphs).strip()


def parse_structured_ocr(image_ark: str, payload: dict[str, Any]) -> StructuredOCRPage:
    if not isinstance(payload, dict):
        raise ValueError("Structured OCR payload must be an object")
    properties = _property_map(payload)
    fs_image_id = properties.get("FS_IMAGE_ID") or None
    dgs = None
    image_number = None
    if fs_image_id:
        try:
            dgs, image_number = parse_fs_image_id(fs_image_id)
        except ValueError:
            pass
    return StructuredOCRPage(
        image_ark=image_ark,
        apid=properties.get("FS_IMAGE_APID") or None,
        fs_image_id=fs_image_id,
        group_id=properties.get("GROUP_APID") or None,
        dgs=dgs,
        dgs_image_number=image_number,
        raw_text=structured_ocr_text(payload),
        raw_json=payload,
    )
