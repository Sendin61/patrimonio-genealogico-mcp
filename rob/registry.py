from .sources import SOURCES, SourceDefinition


def list_territories() -> list[str]:
    return sorted({source.territory for source in SOURCES})


def list_registered_sources() -> list[dict[str, str | None]]:
    return [
        {
            "id": source.id,
            "name": source.name,
            "territory": source.territory,
            "category": source.category,
            "status": source.status.value,
            "connector": source.connector,
            "notes": source.notes,
        }
        for source in SOURCES
    ]


def get_source(source_id: str) -> SourceDefinition | None:
    return next((source for source in SOURCES if source.id == source_id), None)
