from rob.lab.document_domains import detect_document_profiles


def test_detects_notarial_successoral_profile():
    text = """
    En la villa de Carballo ante mi el escribano compareció Manuel Castro, vecino de Buño.
    Digo y declaro que soy hijo legítimo de Domingo Castro y María Calvete. Item mando a
    mis herederos que satisfagan las deudas; nombro por albacea a Antonio Rodríguez.
    Así lo otorgó, siendo testigos los presentes, y doy fe.
    """
    profiles = detect_document_profiles(text)
    assert profiles[0]["key"] == "spain_notarial_successoral"
    assert profiles[0]["score"] > 0
    assert any("parentesco" in reason for reason in profiles[0]["reasons"])


def test_detects_litigation_profile():
    text = """
    En los autos seguidos a instancia de Pedro Varela contra José López, comparece el testigo
    y preguntado por las generales de la ley respondió. Por presentado, únase a los autos.
    """
    profiles = detect_document_profiles(text)
    assert profiles[0]["key"] == "spain_litigation"


def test_generic_world_profile_remains_available():
    text = "John Smith son of William Smith and Mary Brown, resident in Boston."
    profiles = detect_document_profiles(text)
    assert any(profile["key"] == "generic_historical" for profile in profiles)
