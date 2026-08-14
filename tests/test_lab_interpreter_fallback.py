from rob.lab.ai.interpreter import fallback_interpretation


def test_fallback_interprets_pasted_genealogy_profile() -> None:
    text = """búscame datos sobre esta gente de aquí

Manuel Castro Calvete
Ancestro directo (5 generaciones)
1842
Antes de 1895
Eventos
1842
Nacimiento
Buño, A Coruña, Galicia, España
1872
Nacimiento del hijo:
Antonio Castro Rodríguez
1874
Matrimonio con:
Josefa Rodríguez Lema
1875
Nacimiento del hijo:
Domingo Antonio Castro Rodríguez
1880
Nacimiento de la hija:
Asunción Castro Rodríguez
"""
    value = fallback_interpretation(text, reason="TimeoutError")

    assert value["target_candidates"][0]["value"] == "Manuel Castro Calvete"
    assert "Antonio Castro Rodríguez" in value["people_mentions"]
    assert "Josefa Rodríguez Lema" in value["people_mentions"]
    assert "Buño, A Coruña, Galicia, España" in value["places"]
    assert value["chronology"]["year_from"] == 1842
    assert value["chronology"]["year_to"] == 1895
    assert value["ocr_tolerant"] is True
    assert value["multipage_required"] is True
    assert any("TimeoutError" in item for item in value["uncertainties"])
