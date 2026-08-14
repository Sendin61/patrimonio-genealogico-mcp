from __future__ import annotations

from rob.lab.familysearch_ocr import parse_structured_ocr
from rob.lab.ranker import rank_fulltext_entries


def structured_payload(image_id: str = "008159130_02396") -> dict:
    return {
        "stuff": {
            "metadata": {
                "properties": [
                    {"name": "FS_IMAGE_APID", "value": "TH-909-71960-35453-72"},
                    {"name": "FS_IMAGE_ID", "value": image_id},
                    {"name": "GROUP_APID", "value": "M986-12C"},
                ]
            },
            "regions": [
                {
                    "type": "PARAGRAPH",
                    "lines": [
                        {"tokens": [{"text": "hijo"}, {"text": "de"}, {"text": "Domingo"}, {"text": "Varela"}]},
                        {"tokens": [{"text": "y"}, {"text": "Manuela"}, {"text": "Pimentel"}]},
                    ],
                },
                {"type": "CRUFT", "lines": [{"tokens": [{"text": "⌨"}]}]},
            ],
        },
        "recordSet": {},
    }


def test_structured_ocr_extracts_text_and_dgs_metadata() -> None:
    page = parse_structured_ocr("3:1:TEST", structured_payload())
    assert page.dgs == "008159130"
    assert page.dgs_image_number == 2396
    assert page.group_id == "M986-12C"
    assert "Domingo Varela" in page.raw_text
    assert "⌨" not in page.raw_text


def test_ranker_rewards_target_family_place_and_kinship() -> None:
    entries = [
        {
            "id": "3:1:GOOD",
            "collectionTitle": "España, Galicia, Protocolos notariales",
            "content": {
                "title": "Arzúa. Notarial Records 1800",
                "recordPlace": "Arzúa, A Coruña, Galicia, España",
                "recordDate": "1800",
                "textDocument": "Andrés Quintás Varela hijo legítimo de Domingo Varela y Manuela Pimentel",
                "highlightTexts": ["Andrés Quintás Varela"],
                "entities": [{"type": "NAME", "value": "Andrés Quintás Varela"}],
            },
        },
        {
            "id": "3:1:NOISE",
            "collectionTitle": "Otra colección",
            "content": {"textDocument": "texto sin relación aparente", "highlightTexts": [], "entities": []},
        },
    ]
    interpretation = {
        "target_candidates": [{"value": "Andrés Quintás Varela"}],
        "people_mentions": ["Manuela Pimentel"],
        "places": ["Arzúa"],
        "chronology": {"around": 1800},
    }
    ranked = rank_fulltext_entries(entries, interpretation)
    assert ranked[0].source_key == "3:1:GOOD"
    assert ranked[0].score > ranked[1].score
    assert ranked[0].score >= 70
