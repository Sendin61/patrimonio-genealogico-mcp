import re
import unicodedata
from .models import GenealogyQuery


def remove_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def compact_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def expand_query(query: GenealogyQuery) -> list[str]:
    values: list[str] = []

    def add(value: str | None) -> None:
        if not value:
            return
        value = compact_spaces(value)
        if value and value.casefold() not in {item.casefold() for item in values}:
            values.append(value)

    add(query.name)
    add(remove_accents(query.name))

    parts = compact_spaces(query.name).split()
    if len(parts) >= 3:
        add(" ".join([parts[0], parts[-1]]))
        add(" ".join(parts[1:]))

    for variant in query.variants:
        add(variant)
        add(remove_accents(variant))

    if query.spouse:
        add(f'"{query.name}" "{query.spouse}"')

    for place in query.places:
        add(f'"{query.name}" "{place}"')

    if query.profession:
        add(f'"{query.name}" "{query.profession}"')

    for term in query.extra_terms:
        add(f'"{query.name}" "{term}"')

    return values
