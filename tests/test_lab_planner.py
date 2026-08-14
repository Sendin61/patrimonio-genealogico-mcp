from __future__ import annotations

from rob.lab.planner import build_research_plan


def test_plan_uses_candidates_places_family_and_ocr_wildcards() -> None:
    plan = build_research_plan(
        {
            "target_candidates": [
                {"value": "Andrés Quintás Varela", "confidence": 0.94, "reason": "normalización"}
            ],
            "people_mentions": ["Manuela Pimentel"],
            "places": ["Arzúa"],
            "chronology": {"around": 1800, "year_from": None, "year_to": None},
            "ocr_tolerant": True,
        }
    )
    queries = [action.query for action in plan.actions]
    assert '"Andrés Quintás Varela"' in queries
    assert any("+Andrés" in query and "+Arzúa" in query for query in queries if query)
    assert any("Manuela" in query and "Pimentel" in query for query in queries if query)
    assert any("?" in query for query in queries if query)
    assert plan.multipage_required is True
