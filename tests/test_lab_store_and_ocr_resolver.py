from __future__ import annotations

from rob.lab.models import DocumentContext, PageContext
from rob.lab.ocr_resolver import multipage_excerpt
from rob.lab.store import LabStore


def test_store_persists_investigation_and_ocr(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ROB_LAB_HOME", str(tmp_path / "ROB-Genealogy-Lab"))
    store = LabStore()
    investigation_id = store.create_investigation("busca a andres quintas")
    store.update_investigation(
        investigation_id,
        status="interpreted",
        interpretation={"intent": "find_person"},
    )
    store.upsert_ocr_page(
        source="familysearch",
        image_id="3:1:TEST",
        raw_ocr="Andrés Quintás vecino de Arzúa y Manuela Pimentel",
        image_number=42,
    )

    row = store.investigation(investigation_id)
    assert row is not None
    assert row["raw_request"] == "busca a andres quintas"
    assert row["interpretation"]["intent"] == "find_person"
    hits = store.search_ocr('"Andrés"')
    assert hits and hits[0]["image_id"] == "3:1:TEST"


def test_multipage_excerpt_never_drops_whole_pages() -> None:
    pages = [
        PageContext(image_number=i, raw_text=(f"Inicio pagina {i}. " + ("Varela " if i == 7 else "") + "x" * 6000 + f" final pagina {i}."))
        for i in range(5, 10)
    ]
    context = DocumentContext(center_image=7, pages=pages)
    excerpt = multipage_excerpt(context, focus_terms=["Varela", "Bea"], maximum_characters=12000)
    for i in range(5, 10):
        assert f"[[IMG {i}]]" in excerpt
    assert "Varela" in excerpt
