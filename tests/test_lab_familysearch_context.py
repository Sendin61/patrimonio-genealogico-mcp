from __future__ import annotations

import asyncio

from rob.lab.ai.base import AIProviderStatus
from rob.lab.bridge import BridgeCommandType, BridgeResult
from rob.lab.familysearch_context import FamilySearchDocumentReader
from rob.lab.familysearch_ids import dgs_image_to_ark
from rob.lab.store import LabStore


class UnusedProvider:
    async def status(self) -> AIProviderStatus:
        return AIProviderStatus(True, "fake", "http://local", "fake")

    async def chat(self, messages, *, temperature=0.1, max_tokens=2048, json_schema=None):
        raise AssertionError("Continuity AI must not run when extra-page expansion is disabled")


def ocr_payload(number: int) -> dict:
    return {
        "stuff": {
            "metadata": {
                "properties": [
                    {"name": "FS_IMAGE_APID", "value": f"TH-TEST-{number}"},
                    {"name": "FS_IMAGE_ID", "value": f"008159130_{number:05d}"},
                    {"name": "GROUP_APID", "value": "M986-12C"},
                ]
            },
            "regions": [
                {
                    "type": "PARAGRAPH",
                    "lines": [
                        {"tokens": [{"text": "Texto"}, {"text": "imagen"}, {"text": str(number)}]}
                    ],
                }
            ],
        }
    }


async def _scenario(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROB_LAB_HOME", str(tmp_path / "ROB-Genealogy-Lab"))
    store = LabStore()
    center = 2396
    center_ark = "3:1:CENTRAL"
    expected = {dgs_image_to_ark("008159130", number): number for number in range(center - 3, center + 4) if number != center}

    async def bridge_call(kind: BridgeCommandType, payload: dict):
        if kind == BridgeCommandType.STRUCTURED_OCR:
            assert payload["image_id"] == center_ark
            return BridgeResult("central", True, {"json": ocr_payload(center)})
        if kind == BridgeCommandType.OCR_PAGES:
            items = payload["items"]
            assert {item["image_id"] for item in items} == set(expected)
            return BridgeResult(
                "batch",
                True,
                {
                    "pages": [
                        {
                            "image_id": item["image_id"],
                            "ok": True,
                            "json": ocr_payload(expected[item["image_id"]]),
                        }
                        for item in items
                    ]
                },
            )
        raise AssertionError(kind)

    reader = FamilySearchDocumentReader(
        bridge_call=bridge_call,
        provider=UnusedProvider(),
        store=store,
        initial_radius=3,
        max_extra_pages_each_side=0,
    )
    context = await reader.read(center_ark)
    assert context.center_image == center
    assert context.estimated_start_image == center - 3
    assert context.estimated_end_image == center + 3
    assert [page.image_number for page in context.pages] == list(range(center - 3, center + 4))
    assert all(page.raw_text.startswith("Texto imagen") for page in context.pages)
    assert store.search_ocr('"Texto"', limit=20)


def test_familysearch_reader_builds_initial_seven_page_context(monkeypatch, tmp_path) -> None:
    asyncio.run(_scenario(tmp_path, monkeypatch))
