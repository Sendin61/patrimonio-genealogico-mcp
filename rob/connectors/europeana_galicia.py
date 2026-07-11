from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..models import GenealogyQuery, SearchResult


EUROPEANA_SEARCH_URL = "https://api.europeana.eu/record/v2/search.json"
GALICIANA_DATA_PROVIDER = "Galiciana: Digital Library of Galicia"
GALICIANA_BDG_COLLECTION = "2022706_Ag_ES_Hispana_gal"


@dataclass(slots=True)
class EuropeanaGaliciaReport:
    query: str
    filters: list[str]
    total_api: int = 0
    results: list[SearchResult] = field(default_factory=list)
    status: str = "ok"
    error_type: str | None = None
    error: str | None = None


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(value.casefold().split())


def _lucene_phrase(value: str) -> str:
    clean = " ".join(value.replace('"', " ").split())
    return f'"{clean}"'


def _name_variants(query: GenealogyQuery, maximum: int = 6) -> list[str]:
    candidates = [query.name, *query.variants]
    output: list[str] = []
    seen: set[str] = set()

    for candidate in candidates:
        clean = " ".join(candidate.replace('"', " ").split())
        if len(clean) < 3:
            continue

        for version in (clean, _remove_accents(clean)):
            key = version.casefold()
            if key in seen:
                continue
            seen.add(key)
            output.append(version)
            if len(output) >= maximum:
                return output

    return output


def _remove_accents(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    return "".join(char for char in value if not unicodedata.combining(char))


def build_europeana_query(query: GenealogyQuery) -> str:
    variants = _name_variants(query)
    if not variants:
        variants = [query.name]

    phrases = [_lucene_phrase(value) for value in variants]

    canonical_tokens = [
        token
        for token in re.findall(r"[\wÀ-ÿ'-]+", _remove_accents(query.name))
        if len(token) >= 2
    ]
    if len(canonical_tokens) >= 2:
        token_clause = " AND ".join(_lucene_phrase(token) for token in canonical_tokens)
        phrases.append(f"({token_clause})")

    return "(" + " OR ".join(phrases) + ")"


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _extract_year(values: list[str]) -> int | None:
    for value in values:
        match = re.search(r"(?<!\d)(1[0-9]{3}|20[0-9]{2})(?!\d)", value)
        if match:
            return int(match.group(1))
    return None


def _metadata_text(item: dict[str, Any]) -> str:
    fields = (
        _as_list(item.get("title"))
        + _as_list(item.get("dcCreator"))
        + _as_list(item.get("dcDescription"))
        + _as_list(item.get("edmPlaceLabel"))
        + _as_list(item.get("dcSubject"))
    )
    return _normalise(" ".join(fields))


def _score_item(
    item: dict[str, Any],
    query: GenealogyQuery,
) -> tuple[float, list[str]]:
    text = _metadata_text(item)
    canonical = _normalise(query.name)
    variants = [_normalise(value) for value in _name_variants(query)]

    score = 0.35
    reasons: list[str] = []

    if canonical and canonical in text:
        score += 0.40
        reasons.append("nombre completo en los metadatos")
    elif any(value and value in text for value in variants):
        score += 0.30
        reasons.append("variante del nombre en los metadatos")
    else:
        tokens = [token for token in canonical.split() if len(token) >= 2]
        matched = sum(1 for token in tokens if token in text)
        if tokens:
            score += min(0.25, 0.25 * matched / len(tokens))
            reasons.append(f"{matched}/{len(tokens)} componentes del nombre")

    places = [_normalise(value) for value in query.places]
    if any(place and place in text for place in places):
        score += 0.10
        reasons.append("lugar compatible")

    dates = _as_list(item.get("year")) or _as_list(item.get("dcDate"))
    year = _extract_year(dates)
    if year is not None:
        if query.year_from is not None and year < query.year_from:
            score -= 0.10
            reasons.append("fecha anterior al intervalo indicado")
        elif query.year_to is not None and year > query.year_to:
            score -= 0.10
            reasons.append("fecha posterior al intervalo indicado")
        else:
            score += 0.05
            reasons.append("fecha compatible")

    data_provider = _as_list(item.get("dataProvider"))
    if GALICIANA_DATA_PROVIDER in data_provider:
        score += 0.05
        reasons.append("proveedor Galiciana confirmado")

    return max(0.0, min(score, 1.0)), reasons


def parse_europeana_item(
    item: dict[str, Any],
    query: GenealogyQuery,
) -> SearchResult:
    titles = _as_list(item.get("title"))
    creators = _as_list(item.get("dcCreator"))
    descriptions = _as_list(item.get("dcDescription"))
    dates = _as_list(item.get("year")) or _as_list(item.get("dcDate"))
    places = _as_list(item.get("edmPlaceLabel"))
    score, reasons = _score_item(item, query)

    item_id = str(item.get("id") or "")
    url = str(item.get("guid") or "")
    if not url and item_id:
        url = f"https://www.europeana.eu/item{item_id}"

    return SearchResult(
        source_id="galiciana_europeana",
        source_name="Galiciana vía Europeana",
        territory="Galicia",
        title=titles[0] if titles else "Registro sin título",
        url=url or EUROPEANA_SEARCH_URL,
        matched_text=(descriptions[0] if descriptions else None)
        or (creators[0] if creators else None),
        date=dates[0] if dates else None,
        place=places[0] if places else None,
        document_type=str(item.get("type") or "objeto cultural"),
        score=score,
        score_reasons=reasons or ["coincidencia devuelta por Europeana"],
        raw=item,
    )


class EuropeanaGaliciaConnector:
    source_id = "galiciana_europeana"

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise RuntimeError("Falta EUROPEANA_API_KEY.")
        self.api_key = api_key.strip()
        self.timeout = timeout
        self.transport = transport

    async def search(
        self,
        query: GenealogyQuery,
        limit: int = 20,
    ) -> EuropeanaGaliciaReport:
        limit = max(1, min(limit, 100))
        europeana_query = build_europeana_query(query)
        filters = [
            f'DATA_PROVIDER:"{GALICIANA_DATA_PROVIDER}"',
            f'europeana_collectionName:"{GALICIANA_BDG_COLLECTION}"',
        ]

        params: list[tuple[str, str | int]] = [
            ("wskey", self.api_key),
            ("query", europeana_query),
            ("rows", limit),
            ("start", 1),
            ("profile", "rich"),
            ("qf", filters[0]),
            ("qf", filters[1]),
        ]

        report = EuropeanaGaliciaReport(
            query=europeana_query,
            filters=filters,
        )

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                transport=self.transport,
                headers={"User-Agent": "RobGenealogia/0.4.0"},
            ) as client:
                response = await client.get(EUROPEANA_SEARCH_URL, params=params)
                response.raise_for_status()
                payload = response.json()

            items = payload.get("items", [])
            if not isinstance(items, list):
                items = []

            report.total_api = int(payload.get("totalResults") or 0)
            report.results = sorted(
                (
                    parse_europeana_item(item, query)
                    for item in items
                    if isinstance(item, dict)
                ),
                key=lambda result: (-result.score, result.date or "", result.title),
            )[:limit]
            return report

        except Exception as exc:
            report.status = "unavailable"
            report.error_type = type(exc).__name__
            report.error = str(exc).strip() or repr(exc)
            return report
