from rob.sources import TERRITORIES, SOURCES, source_summary


def test_all_spanish_territories_are_registered() -> None:
    assert len(TERRITORIES) == 19
    registered = {source.territory for source in SOURCES}
    for territory in TERRITORIES:
        assert territory in registered


def test_status_summary_counts_all_sources() -> None:
    assert sum(source_summary().values()) == len(SOURCES)
