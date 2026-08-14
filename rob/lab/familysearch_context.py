from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .ai.base import AIProvider
from .bridge import BridgeCommandType, BridgeResult
from .continuity import AIContinuityDecider
from .document_context import DocumentContextBuilder
from .familysearch_ids import dgs_image_to_ark
from .familysearch_ocr import StructuredOCRPage, parse_structured_ocr
from .models import DocumentContext, PageContext
from .store import LabStore


BridgeCall = Callable[[BridgeCommandType, dict[str, Any]], Awaitable[BridgeResult]]


class FamilySearchDocumentReader:
    """Reconstruct a multi-page FamilySearch document around one image ARK."""

    def __init__(
        self,
        *,
        bridge_call: BridgeCall,
        provider: AIProvider,
        store: LabStore | None = None,
        initial_radius: int = 3,
        max_extra_pages_each_side: int = 6,
    ) -> None:
        self.bridge_call = bridge_call
        self.provider = provider
        self.store = store
        self.initial_radius = initial_radius
        self.max_extra_pages_each_side = max_extra_pages_each_side

    async def _fetch_one(self, ark: str) -> StructuredOCRPage | None:
        result = await self.bridge_call(
            BridgeCommandType.STRUCTURED_OCR,
            {"image_id": ark},
        )
        if not result.ok:
            return None
        payload = result.payload.get("json") if isinstance(result.payload, dict) else None
        if not isinstance(payload, dict):
            return None
        try:
            return parse_structured_ocr(ark, payload)
        except (TypeError, ValueError):
            return None

    def _save(self, page: StructuredOCRPage) -> None:
        if self.store is None:
            return
        self.store.upsert_ocr_page(
            source="familysearch",
            image_id=page.image_ark,
            raw_ocr=page.raw_text,
            ark=page.image_ark,
            dgs=page.dgs,
            image_number=page.dgs_image_number,
            structured=page.raw_json,
            metadata={
                "apid": page.apid,
                "fs_image_id": page.fs_image_id,
                "group_id": page.group_id,
            },
        )

    async def read(self, center_ark: str) -> DocumentContext:
        central = await self._fetch_one(center_ark)
        if central is None:
            raise LookupError(f"No hay OCR estructurado accesible para {center_ark}")
        self._save(central)
        if not central.dgs or not central.dgs_image_number:
            raise LookupError("El OCR estructurado no expuso FS_IMAGE_ID/DGS para construir vecinos")

        dgs = central.dgs
        center_number = central.dgs_image_number
        cache: dict[int, PageContext | None] = {
            center_number: central.to_page_context()
        }

        numbers = list(
            range(
                max(1, center_number - self.initial_radius),
                center_number + self.initial_radius + 1,
            )
        )
        initial_items = [
            {"image_id": dgs_image_to_ark(dgs, number)}
            for number in numbers
            if number != center_number
        ]
        if initial_items:
            batch = await self.bridge_call(
                BridgeCommandType.OCR_PAGES,
                {"items": initial_items},
            )
            pages = (
                batch.payload.get("pages")
                if batch.ok and isinstance(batch.payload, dict)
                and isinstance(batch.payload.get("pages"), list)
                else []
            )
            for item in pages:
                if not isinstance(item, dict) or not item.get("ok"):
                    continue
                ark = str(item.get("image_id") or "")
                payload = item.get("json")
                if not ark or not isinstance(payload, dict):
                    continue
                try:
                    page = parse_structured_ocr(ark, payload)
                except (TypeError, ValueError):
                    continue
                if page.dgs_image_number:
                    cache[page.dgs_image_number] = page.to_page_context()
                    self._save(page)

        async def load(number: int) -> PageContext | None:
            if number in cache:
                return cache[number]
            try:
                ark = dgs_image_to_ark(dgs, number)
            except ValueError:
                cache[number] = None
                return None
            page = await self._fetch_one(ark)
            if page is None:
                cache[number] = None
                return None
            self._save(page)
            context = page.to_page_context()
            cache[number] = context
            return context

        builder = DocumentContextBuilder(
            load,
            AIContinuityDecider(self.provider),
            initial_radius=self.initial_radius,
            max_extra_pages_each_side=self.max_extra_pages_each_side,
        )
        return await builder.build(center_number)
