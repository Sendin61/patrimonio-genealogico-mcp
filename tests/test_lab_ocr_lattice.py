from rob.lab.ocr_lattice import OCRObservation, OCRVariantFamily, cluster_observations


def test_many_recurrent_variants_remain_first_class_evidence():
    readings = [
        "Varela", "Varela", "Varela", "Varela",
        "Barela", "Barela", "Barela",
        "Bareia", "Bareia",
        "Bea", "Vreła", "arela",
    ]
    family = OCRVariantFamily(
        id="surname_domingo",
        observations=[OCRObservation(raw=value, role_hint="surname") for value in readings],
    )
    counts = family.counts()
    assert counts["Varela"] == 4
    assert counts["Barela"] == 3
    assert counts["Bareia"] == 2
    assert len(counts) == 6
    assert family.recurrent_variants() == [("Varela", 4), ("Barela", 3), ("Bareia", 2)]
    assert family.instability() > 0.25
    assert family.needs_visual_verification(high_impact=True)


def test_context_can_link_lexically_distant_readings():
    observations = [
        OCRObservation(raw="Bea", role_hint="father_surname"),
        OCRObservation(raw="Varela", role_hint="father_surname"),
    ]
    families = cluster_observations(
        observations,
        contextual_links={(0, 1): 0.94},
    )
    assert len(families) == 1
    assert {obs.raw for obs in families[0].observations} == {"Bea", "Varela"}


def test_lexically_distant_readings_do_not_merge_without_context():
    observations = [OCRObservation(raw="Bea"), OCRObservation(raw="Varela")]
    families = cluster_observations(observations)
    assert len(families) == 2
