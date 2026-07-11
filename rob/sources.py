from dataclasses import dataclass
from .models import SourceStatus


@dataclass(frozen=True, slots=True)
class SourceDefinition:
    id: str
    name: str
    territory: str
    category: str
    status: SourceStatus = SourceStatus.REGISTERED
    connector: str | None = None
    notes: str = ""


TERRITORIES = (
    "Andalucía",
    "Aragón",
    "Principado de Asturias",
    "Illes Balears",
    "Canarias",
    "Cantabria",
    "Castilla-La Mancha",
    "Castilla y León",
    "Cataluña",
    "Comunitat Valenciana",
    "Extremadura",
    "Galicia",
    "Comunidad de Madrid",
    "Región de Murcia",
    "Comunidad Foral de Navarra",
    "País Vasco",
    "La Rioja",
    "Ceuta",
    "Melilla",
)


SOURCES = (
    SourceDefinition("andalucia", "Fuentes digitales de Andalucía", "Andalucía", "autonómica"),
    SourceDefinition("aragon", "Fuentes digitales de Aragón", "Aragón", "autonómica"),
    SourceDefinition("asturias", "Fuentes digitales de Asturias", "Principado de Asturias", "autonómica"),
    SourceDefinition("baleares", "Fuentes digitales de Illes Balears", "Illes Balears", "autonómica"),
    SourceDefinition("canarias", "Fuentes digitales de Canarias", "Canarias", "autonómica"),
    SourceDefinition("cantabria", "Fuentes digitales de Cantabria", "Cantabria", "autonómica"),
    SourceDefinition("castilla_la_mancha", "Fuentes digitales de Castilla-La Mancha", "Castilla-La Mancha", "autonómica"),
    SourceDefinition("castilla_y_leon", "Fuentes digitales de Castilla y León", "Castilla y León", "autonómica"),
    SourceDefinition("cataluna", "Fuentes digitales de Cataluña", "Cataluña", "autonómica"),
    SourceDefinition("comunitat_valenciana", "Fuentes digitales de la Comunitat Valenciana", "Comunitat Valenciana", "autonómica"),
    SourceDefinition("extremadura", "Fuentes digitales de Extremadura", "Extremadura", "autonómica"),
    SourceDefinition(
        "galiciana_bdg",
        "Galiciana. Biblioteca Dixital de Galicia",
        "Galicia",
        "biblioteca digital",
        status=SourceStatus.DEVELOPMENT,
        connector="rob.connectors.galiciana_bdg.GalicianaBDGConnector",
        notes="SPARQL y OAI-PMH instalados; falta validación desde Render y acceso directo al OCR.",
    ),
    SourceDefinition(
        "galiciana_adg",
        "Galiciana. Arquivo Dixital de Galicia",
        "Galicia",
        "archivo digital",
        status=SourceStatus.DEVELOPMENT,
        connector=None,
        notes="OAI-PMH instalado para identificación; la búsqueda requerirá índice local o integración del formulario.",
    ),
    SourceDefinition(
        "galiciana_prensa",
        "Prensa histórica de Galiciana",
        "Galicia",
        "hemeroteca",
        status=SourceStatus.REGISTERED,
        notes="Pendiente de conectar la búsqueda OCR a texto completo.",
    ),
    SourceDefinition("madrid", "Fuentes digitales de Madrid", "Comunidad de Madrid", "autonómica"),
    SourceDefinition("murcia", "Fuentes digitales de Murcia", "Región de Murcia", "autonómica"),
    SourceDefinition("navarra", "Fuentes digitales de Navarra", "Comunidad Foral de Navarra", "autonómica"),
    SourceDefinition("pais_vasco", "Fuentes digitales del País Vasco", "País Vasco", "autonómica"),
    SourceDefinition("la_rioja", "Fuentes digitales de La Rioja", "La Rioja", "autonómica"),
    SourceDefinition("ceuta", "Fuentes digitales de Ceuta", "Ceuta", "ciudad autónoma"),
    SourceDefinition("melilla", "Fuentes digitales de Melilla", "Melilla", "ciudad autónoma"),
    SourceDefinition(
        "europeana",
        "Europeana",
        "Europa",
        "supranacional",
        status=SourceStatus.FUNCTIONAL,
        connector="servidor heredado Europeana",
    ),
)


def get_sources(territory: str | None = None) -> list[SourceDefinition]:
    if territory is None:
        return list(SOURCES)
    wanted = territory.casefold().strip()
    return [source for source in SOURCES if source.territory.casefold() == wanted]


def source_summary() -> dict[str, int]:
    result: dict[str, int] = {}
    for source in SOURCES:
        result[source.status.value] = result.get(source.status.value, 0) + 1
    return result
