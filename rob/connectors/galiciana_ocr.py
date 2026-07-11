from __future__ import annotations

import ast
import asyncio
import base64
import html as html_lib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Comment

from ..models import GenealogyQuery


BASE_URL = "https://biblioteca.galiciana.gal"
SEARCH_URL = f"{BASE_URL}/es/consulta/resultados_ocr.do"
SEARCH_REFERER = f"{BASE_URL}/es/consulta/busqueda.do"
ALLOWED_HOST = "biblioteca.galiciana.gal"

DEFAULT_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "es-ES,es;q=0.9,gl;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
}

MONTHS = {
    "xaneiro": 1,
    "enero": 1,
    "febreiro": 2,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "maio": 5,
    "mayo": 5,
    "xuño": 6,
    "xuno": 6,
    "junio": 6,
    "xullo": 7,
    "julio": 7,
    "agosto": 8,
    "setembro": 9,
    "septiembre": 9,
    "outubro": 10,
    "octubre": 10,
    "novembro": 11,
    "noviembre": 11,
    "decembro": 12,
    "diciembre": 12,
}

CATEGORY_PATTERNS: dict[str, tuple[str, ...]] = {
    "militar": (
        "capitán",
        "capitan",
        "teniente",
        "batallón",
        "batallon",
        "depósito",
        "deposito",
        "ayudante",
        "primer teniente",
        "arma",
    ),
    "judicial": (
        "juzgado",
        "juzgados",
        "juez",
        "fiscal",
        "edicto",
        "actuario",
        "subasta",
        "emplaza",
        "procesado",
        "causa",
    ),
    "cargo municipal": (
        "concejal",
        "concejales",
        "ayuntamiento",
        "corporación",
        "corporacion",
        "hacienda municipal",
        "comisión",
        "comision",
    ),
    "propiedad": (
        "propiedad",
        "finca",
        "linda",
        "por el norte",
        "por el sur",
        "por el este",
        "por el oeste",
        "apreciada en",
        "teja",
    ),
    "familia": (
        "hijos de",
        "hijo de",
        "hija de",
        "viuda de",
        "herederos de",
        "su esposa",
        "su mujer",
    ),
    "residencia o vecindad": (
        "vecino de",
        "veciño de",
        "parroquia de",
        "de viana",
        "deviana",
        "san pedro de viana",
    ),
    "aportación económica": (
        "suscripción",
        "suscripcion",
        "donativo",
        "pesetas",
        "recaudación",
        "recaudacion",
    ),
    "lista o padrón": (
        "padrón",
        "padron",
        "lista",
        "número de orden",
        "numero de orden",
        "electores",
        "contribuyentes",
    ),
}

CATEGORY_SUMMARIES = {
    "militar": "Aparece en contextos militares o identificado mediante empleos del Ejército.",
    "judicial": "Aparece en actuaciones, anuncios o funciones de carácter judicial.",
    "cargo municipal": "Aparece vinculado a la administración o corporación municipal.",
    "propiedad": "Aparece relacionado con bienes, lindes, daños o valoraciones patrimoniales.",
    "familia": "La fuente contiene una referencia familiar directa.",
    "residencia o vecindad": "La fuente aporta indicios de residencia, parroquia o vecindad.",
    "aportación económica": "Aparece en una relación de cantidades, aportaciones o recaudación.",
    "lista o padrón": "Aparece incluido en una lista, padrón o relación nominal.",
}


@dataclass(slots=True)
class OCRInterpretation:
    category: str
    statement: str
    indicators: list[str]
    confidence: float


@dataclass(slots=True)
class GalicianaOCRMention:
    record_id: str
    title: str
    parent_publication: str | None
    date: str | None
    page: str | None
    record_url: str | None
    page_url: str
    digital_copy_url: str | None
    pdf_url: str | None
    path: str | None
    image_id: str | None
    snippets: list[str]
    matched_query: str
    score: float
    score_reasons: list[str]
    interpretations: list[OCRInterpretation]


@dataclass(slots=True)
class OCRSearchDiagnostic:
    query: str
    ok: bool
    total_reported: int = 0
    mentions_parsed: int = 0
    pages_read: int = 0
    challenge_solved: bool = False
    error_type: str | None = None
    error: str | None = None


@dataclass(slots=True)
class GalicianaOCRReport:
    status: str
    queries: list[str]
    diagnostics: list[OCRSearchDiagnostic]
    mentions: list[GalicianaOCRMention]
    findings: list[dict[str, Any]]
    chronology: list[dict[str, Any]]
    total_unique: int
    note: str


@dataclass(slots=True)
class ParsedResultsPage:
    search_id: str | None
    total_reported: int
    mentions: list[GalicianaOCRMention]
    next_url: str | None


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(value)).strip()


def _remove_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _normalise(value: str) -> str:
    return _compact(_remove_accents(value).casefold())


def _add_unique(values: list[str], value: str, *, limit: int) -> None:
    value = _compact(value)
    if not value:
        return
    if value.casefold() not in {item.casefold() for item in values}:
        values.append(value)
    if len(values) > limit:
        del values[limit:]


def build_ocr_queries(query: GenealogyQuery, maximum: int = 6) -> list[str]:
    """Build a small, high-recall set of Galiciana full-text queries."""
    maximum = max(1, min(maximum, 10))
    values: list[str] = []

    canonical = _compact(query.name)
    canonical_plain = _remove_accents(canonical)

    # Galiciana's OCR index has proved especially effective with the
    # accentless exact form, so it is intentionally attempted first.
    _add_unique(values, f'"{canonical_plain.casefold()}"', limit=maximum)
    _add_unique(values, f'"{canonical.casefold()}"', limit=maximum)

    for variant in query.variants:
        _add_unique(values, f'"{_remove_accents(variant).casefold()}"', limit=maximum)
        _add_unique(values, f'"{variant.casefold()}"', limit=maximum)

    parts = canonical_plain.casefold().split()
    if len(parts) >= 3:
        first = parts[0]
        surnames = " ".join(parts[1:])
        _add_unique(values, f'{first} "{surnames}"', limit=maximum)
        _add_unique(values, f'"{surnames}"', limit=maximum)

        # The final-character wildcard is useful for common OCR errors such
        # as Eiriz/Eiris. It is deliberately not the first query.
        last = parts[-1]
        if len(last) >= 4:
            wildcard = " ".join([*parts[:-1], last[:-1] + "?"])
            _add_unique(values, wildcard, limit=maximum)

    return values[:maximum]


def _extract_date(title: str) -> str | None:
    numeric = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", title)
    if numeric:
        day, month, year = map(int, numeric.groups())
        return f"{year:04d}-{month:02d}-{day:02d}"

    lowered = _normalise(title)
    month_names = "|".join(sorted(MONTHS, key=len, reverse=True))
    textual = re.search(
        rf"\b(1[6-9]\d{{2}}|20\d{{2}})\s+({month_names})\s+(\d{{1,2}})\b",
        lowered,
    )
    if textual:
        year = int(textual.group(1))
        month = MONTHS[textual.group(2)]
        day = int(textual.group(3))
        return f"{year:04d}-{month:02d}-{day:02d}"

    year_only = re.search(r"\b(1[6-9]\d{2}|20\d{2})\b", title)
    return year_only.group(1) if year_only else None


def _extract_year(date: str | None) -> int | None:
    if not date:
        return None
    match = re.search(r"\b(1[6-9]\d{2}|20\d{2})\b", date)
    return int(match.group(1)) if match else None


def interpret_snippets(snippets: list[str]) -> list[OCRInterpretation]:
    text = _normalise(" ".join(snippets))
    output: list[OCRInterpretation] = []

    for category, patterns in CATEGORY_PATTERNS.items():
        indicators = [pattern for pattern in patterns if _normalise(pattern) in text]
        if not indicators:
            continue
        confidence = min(0.98, 0.72 + 0.06 * len(indicators))
        output.append(
            OCRInterpretation(
                category=category,
                statement=CATEGORY_SUMMARIES[category],
                indicators=indicators,
                confidence=round(confidence, 2),
            )
        )

    if not output:
        output.append(
            OCRInterpretation(
                category="mención nominal",
                statement=(
                    "La página contiene una mención nominal, pero el fragmento "
                    "no basta para atribuirle un hecho concreto."
                ),
                indicators=[],
                confidence=0.55,
            )
        )

    return output


def _score_mention(
    mention_text: str,
    date: str | None,
    query: GenealogyQuery,
) -> tuple[float, list[str]]:
    text = _normalise(mention_text)
    canonical = _normalise(query.name)
    variants = [_normalise(item) for item in query.variants]
    tokens = [token for token in canonical.split() if len(token) >= 2]

    score = 0.25
    reasons: list[str] = []

    if canonical and canonical in text:
        score += 0.50
        reasons.append("nombre completo en el OCR")
    elif any(variant and variant in text for variant in variants):
        score += 0.42
        reasons.append("variante completa en el OCR")
    else:
        matched = sum(1 for token in tokens if token in text)
        if tokens:
            score += min(0.35, 0.35 * matched / len(tokens))
            reasons.append(f"{matched}/{len(tokens)} componentes del nombre")

    matching_places = [place for place in query.places if _normalise(place) in text]
    if matching_places:
        score += 0.10
        reasons.append("lugar compatible: " + ", ".join(matching_places[:3]))

    if query.spouse and _normalise(query.spouse) in text:
        score += 0.10
        reasons.append("cónyuge compatible")

    if query.profession and _normalise(query.profession) in text:
        score += 0.08
        reasons.append("profesión compatible")

    year = _extract_year(date)
    if year is not None:
        if query.year_from is not None and year < query.year_from:
            score -= 0.15
            reasons.append("fecha anterior al intervalo")
        elif query.year_to is not None and year > query.year_to:
            score -= 0.15
            reasons.append("fecha posterior al intervalo")
        else:
            score += 0.05
            reasons.append("fecha compatible")

    return round(max(0.0, min(score, 1.0)), 3), reasons


def _safe_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST:
        raise ValueError("La URL no pertenece a biblioteca.galiciana.gal.")
    return url


def _query_value(url: str, name: str) -> str | None:
    values = parse_qs(urlparse(url).query).get(name)
    return values[0] if values else None


def parse_results_html(
    html: str,
    *,
    matched_query: str,
    genealogy_query: GenealogyQuery,
    current_url: str = SEARCH_URL,
) -> ParsedResultsPage:
    soup = BeautifulSoup(html, "html.parser")

    search_id: str | None = None
    for anchor in soup.select('a[href*="resultados_ocr.do"]'):
        candidate = _query_value(urljoin(current_url, anchor.get("href", "")), "id")
        if candidate:
            search_id = candidate
            break

    total_reported = 0
    navigation = soup.select_one(".nav_descrip")
    if navigation:
        total_match = re.search(r"\b\d+\s+al\s+\d+\s+de\s+(\d+)\b", navigation.get_text(" ", strip=True))
        if total_match:
            total_reported = int(total_match.group(1))

    mentions: list[GalicianaOCRMention] = []
    for section in soup.select("ol#nav_registros section.registro_bib"):
        title_anchor = section.select_one("span.titulo a")
        if title_anchor is None:
            continue

        title = _compact(title_anchor.get_text(" ", strip=True))
        record_url = urljoin(current_url, title_anchor.get("href", ""))
        parent_element = section.select_one(".publicacion_madre")
        parent_publication = None
        if parent_element:
            parent_publication = re.sub(
                r"^En:\s*",
                "",
                _compact(parent_element.get_text(" ", strip=True)),
                flags=re.IGNORECASE,
            ) or None

        record_id = ""
        dl = section.select_one("dl[id^='dl_']")
        if dl is not None:
            record_id = str(dl.get("id", "")).removeprefix("dl_")
        if not record_id:
            for comment in section.find_all(string=lambda value: isinstance(value, Comment)):
                match = re.search(r"Id:\s*(\d+)", str(comment))
                if match:
                    record_id = match.group(1)
                    break

        media_links = section.select("a[href*='catalogo_imagenes/grupo.do']")
        digital_copy_url: str | None = None
        pdf_url: str | None = None
        for anchor in media_links:
            label = _normalise(anchor.get_text(" ", strip=True))
            absolute = urljoin(current_url, anchor.get("href", ""))
            if "version pdf" in label:
                pdf_url = absolute
            elif digital_copy_url is None and "idImagen=" not in absolute:
                digital_copy_url = absolute

        for unit in section.select("li.unidad_textual"):
            page_anchor = None
            for anchor in unit.select("a[href*='idImagen=']"):
                anchor_id = str(anchor.get("id", ""))
                if not anchor_id.endswith("-icon"):
                    page_anchor = anchor
                    break
            if page_anchor is None:
                continue

            page_url = urljoin(current_url, page_anchor.get("href", ""))
            page = _compact(page_anchor.get_text(" ", strip=True)) or None
            snippets = [
                _compact(item.get_text(" ", strip=True))
                for item in unit.select("ul.texto_ocurrencias > li")
                if _compact(item.get_text(" ", strip=True))
            ]
            if not snippets:
                snippets = [_compact(unit.get_text(" ", strip=True))]

            date = _extract_date(title)
            score, reasons = _score_mention(" ".join(snippets), date, genealogy_query)
            interpretations = interpret_snippets(snippets)

            mentions.append(
                GalicianaOCRMention(
                    record_id=record_id,
                    title=title,
                    parent_publication=parent_publication,
                    date=date,
                    page=page,
                    record_url=record_url,
                    page_url=page_url,
                    digital_copy_url=digital_copy_url,
                    pdf_url=pdf_url,
                    path=_query_value(page_url, "path"),
                    image_id=_query_value(page_url, "idImagen"),
                    snippets=snippets,
                    matched_query=matched_query,
                    score=score,
                    score_reasons=reasons,
                    interpretations=interpretations,
                )
            )

    next_url: str | None = None
    nav = soup.select_one("div.nav_paginas2") or soup.select_one("div.nav_paginas")
    if nav:
        forward = nav.select_one("span.nav_alante a[href]")
        if forward:
            next_url = urljoin(current_url, forward.get("href", ""))

    return ParsedResultsPage(
        search_id=search_id,
        total_reported=total_reported,
        mentions=mentions,
        next_url=next_url,
    )


def _encode_packer_token(number: int, base: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwx"
    quotient, remainder = divmod(number, base)
    current = digits[remainder] if remainder <= 33 else chr(remainder + 31)
    return (_encode_packer_token(quotient, base) if quotient else "") + current


def unpack_dean_edwards_packer(script: str) -> str:
    """Unpack the classic eval(function(p,a,c,k,e,d){...}) wrapper."""
    invocation = re.search(
        r"\}\(\s*'(?P<payload>(?:\\.|[^'])*)'\s*,\s*"
        r"(?P<base>\d+)\s*,\s*(?P<count>\d+)\s*,\s*"
        r"'(?P<keys>(?:\\.|[^'])*)'\.split\('\|'\)\s*,\s*0\s*,\s*\{\}\s*\)\s*\)",
        script,
        flags=re.DOTALL,
    )
    if invocation is None:
        raise ValueError("No se reconoció el empaquetado JavaScript del desafío.")

    payload = ast.literal_eval("'" + invocation.group("payload") + "'")
    keys_text = ast.literal_eval("'" + invocation.group("keys") + "'")
    base = int(invocation.group("base"))
    count = int(invocation.group("count"))
    keys = keys_text.split("|")

    for index in range(count - 1, -1, -1):
        if index >= len(keys) or not keys[index]:
            continue
        token = _encode_packer_token(index, base)
        payload = re.sub(rf"\b{re.escape(token)}\b", keys[index], payload)
    return payload


def _decode_js_string(value: str, quote: str) -> str:
    try:
        return ast.literal_eval(quote + value + quote)
    except (SyntaxError, ValueError):
        return value.replace(r"\/", "/").replace(r"\'", "'").replace(r'\"', '"')


def _string_assignments(script: str) -> dict[str, str]:
    assignments: dict[str, str] = {}
    pattern = re.compile(
        r"(?:\bvar\s+)?([A-Za-z_$][\w$]*)\s*=\s*"
        r"(?P<quote>['\"])(?P<value>(?:\\.|(?!\2).)*?)(?P=quote)\s*;",
        flags=re.DOTALL,
    )
    for match in pattern.finditer(script):
        assignments[match.group(1)] = _decode_js_string(
            match.group("value"),
            match.group("quote"),
        )
    return assignments


def extract_antibot_challenge(html: str) -> tuple[str, str, str, str]:
    """Return endpoint, query-cookie name/value and fwb_dat payload."""
    soup = BeautifulSoup(html, "html.parser")
    packed = "\n".join(
        element.get_text("\n", strip=False)
        for element in soup.find_all("script")
        if "eval(function(p" in element.get_text(" ", strip=False)
    )
    if not packed:
        raise ValueError("La respuesta no contiene el desafío anti-bot esperado.")

    script = unpack_dean_edwards_packer(packed)
    assignments = _string_assignments(script)

    endpoint = next(
        (
            value
            for value in assignments.values()
            if "resultados_ocr.do" in value
        ),
        "/es/consulta/resultados_ocr.do",
    )

    cookie_match = re.search(
        r"\+=\s*['\"]\?['\"]\s*\+\s*([A-Za-z_$][\w$]*)\s*"
        r"\+\s*['\"]=['\"]\s*\+\s*[A-Za-z_$][\w$]*\("
        r"([A-Za-z_$][\w$]*)\)",
        script,
    )
    if cookie_match is None:
        raise ValueError("No se pudo extraer el parámetro de sesión anti-bot.")

    cookie_name = assignments.get(cookie_match.group(1), "")
    encoded_cookie_value = assignments.get(cookie_match.group(2), "")
    if not cookie_name or not encoded_cookie_value:
        raise ValueError("El desafío anti-bot no contiene una sesión utilizable.")

    try:
        padded_cookie = encoded_cookie_value + "=" * (-len(encoded_cookie_value) % 4)
        cookie_value = base64.b64decode(padded_cookie).decode("utf-8")
    except Exception as exc:  # pragma: no cover - depends on remote challenge
        raise ValueError("No se pudo decodificar la sesión anti-bot.") from exc

    payload_match = re.search(
        r"['\"]fwb_dat=['\"]\s*\+\s*"
        r"(?:['\"](?P<literal>[A-Za-z0-9+/=]+)['\"]|(?P<variable>[A-Za-z_$][\w$]*))",
        script,
    )
    if payload_match is None:
        raise ValueError("No se pudo extraer fwb_dat del desafío anti-bot.")

    payload = payload_match.group("literal") or assignments.get(
        payload_match.group("variable") or "",
        "",
    )
    if not payload:
        raise ValueError("El desafío anti-bot devolvió fwb_dat vacío.")

    return urljoin(BASE_URL, endpoint), cookie_name, cookie_value, payload


def _is_antibot_page(html: str) -> bool:
    return "eval(function(p" in html and "fwb_dat" in html


async def _resolve_antibot(
    client: httpx.AsyncClient,
    response: httpx.Response,
) -> tuple[httpx.Response, bool]:
    solved = False
    current = response
    for _ in range(2):
        if not _is_antibot_page(current.text):
            return current, solved
        endpoint, cookie_name, cookie_value, payload = extract_antibot_challenge(
            current.text
        )
        current = await client.post(
            endpoint,
            params={cookie_name: cookie_value},
            content=f"fwb_dat={payload}",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": BASE_URL,
                "Referer": SEARCH_URL,
            },
        )
        current.raise_for_status()
        solved = True
    return current, solved


def _mention_key(mention: GalicianaOCRMention) -> tuple[str, str, str]:
    return (
        mention.record_id,
        mention.image_id or mention.page_url,
        _normalise(" ".join(mention.snippets)),
    )


def _aggregate_findings(
    mentions: list[GalicianaOCRMention],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[GalicianaOCRMention]] = {}
    for mention in mentions:
        for interpretation in mention.interpretations:
            grouped.setdefault(interpretation.category, []).append(mention)

    output: list[dict[str, Any]] = []
    for category, items in grouped.items():
        dates = sorted({item.date for item in items if item.date})
        examples = []
        for item in sorted(items, key=lambda value: (value.date or "9999", value.title))[:4]:
            examples.append(
                {
                    "date": item.date,
                    "title": item.title,
                    "page": item.page,
                    "evidence": item.snippets[0] if item.snippets else "",
                    "url": item.page_url,
                }
            )
        output.append(
            {
                "category": category,
                "summary": CATEGORY_SUMMARIES.get(
                    category,
                    "Se han localizado menciones nominales que requieren leer la página completa.",
                ),
                "mention_count": len(items),
                "first_date": dates[0] if dates else None,
                "last_date": dates[-1] if dates else None,
                "examples": examples,
            }
        )

    output.sort(key=lambda item: (-int(item["mention_count"]), str(item["category"])))
    return output


def _build_chronology(
    mentions: list[GalicianaOCRMention],
) -> list[dict[str, Any]]:
    return [
        {
            "date": mention.date,
            "title": mention.title,
            "publication": mention.parent_publication,
            "page": mention.page,
            "evidence": mention.snippets,
            "interpretations": [item.category for item in mention.interpretations],
            "score": mention.score,
            "url": mention.page_url,
        }
        for mention in sorted(
            mentions,
            key=lambda value: (value.date or "9999", value.title, value.page or ""),
        )
    ]


class GalicianaOCRConnector:
    source_id = "galiciana_ocr"

    def __init__(
        self,
        *,
        timeout: float = 45.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.timeout = timeout
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            transport=self.transport,
            headers=DEFAULT_HEADERS,
        )

    async def _search_one(
        self,
        client: httpx.AsyncClient,
        *,
        text_query: str,
        genealogy_query: GenealogyQuery,
        maximum_results: int,
        maximum_pages: int,
    ) -> tuple[list[GalicianaOCRMention], OCRSearchDiagnostic]:
        diagnostic = OCRSearchDiagnostic(query=text_query, ok=False)
        try:
            response = await client.post(
                SEARCH_URL,
                data={"general_ocr": "on", "busq_general": text_query},
                headers={"Origin": BASE_URL, "Referer": SEARCH_REFERER},
            )
            response.raise_for_status()
            response, solved = await _resolve_antibot(client, response)
            diagnostic.challenge_solved = solved

            all_mentions: list[GalicianaOCRMention] = []
            visited: set[str] = set()
            current_url = str(response.url)
            current_html = response.text

            for _ in range(maximum_pages):
                parsed = parse_results_html(
                    current_html,
                    matched_query=text_query,
                    genealogy_query=genealogy_query,
                    current_url=current_url,
                )
                diagnostic.pages_read += 1
                diagnostic.total_reported = max(
                    diagnostic.total_reported,
                    parsed.total_reported,
                )
                all_mentions.extend(parsed.mentions)

                if len(all_mentions) >= maximum_results or not parsed.next_url:
                    break
                if parsed.next_url in visited:
                    break
                visited.add(parsed.next_url)

                next_response = await client.get(
                    parsed.next_url,
                    headers={"Referer": current_url},
                )
                next_response.raise_for_status()
                next_response, next_solved = await _resolve_antibot(
                    client,
                    next_response,
                )
                diagnostic.challenge_solved = diagnostic.challenge_solved or next_solved
                current_url = str(next_response.url)
                current_html = next_response.text

            diagnostic.ok = True
            diagnostic.mentions_parsed = len(all_mentions[:maximum_results])
            return all_mentions[:maximum_results], diagnostic

        except Exception as exc:
            diagnostic.error_type = type(exc).__name__
            diagnostic.error = str(exc).strip() or repr(exc)
            return [], diagnostic

    async def investigate(
        self,
        query: GenealogyQuery,
        *,
        maximum_queries: int = 6,
        maximum_results: int = 120,
        maximum_pages_per_query: int = 5,
    ) -> GalicianaOCRReport:
        queries = build_ocr_queries(query, maximum=maximum_queries)
        diagnostics: list[OCRSearchDiagnostic] = []
        unique: dict[tuple[str, str, str], GalicianaOCRMention] = {}

        async with self._client() as client:
            for index, text_query in enumerate(queries):
                remaining = max(1, maximum_results - len(unique))
                mentions, diagnostic = await self._search_one(
                    client,
                    text_query=text_query,
                    genealogy_query=query,
                    maximum_results=remaining,
                    maximum_pages=maximum_pages_per_query,
                )
                diagnostics.append(diagnostic)
                for mention in mentions:
                    key = _mention_key(mention)
                    previous = unique.get(key)
                    if previous is None or mention.score > previous.score:
                        unique[key] = mention
                if len(unique) >= maximum_results:
                    break
                if index < len(queries) - 1:
                    await asyncio.sleep(0.25)

        mentions = sorted(
            unique.values(),
            key=lambda item: (-item.score, item.date or "9999", item.title),
        )
        successful = sum(1 for item in diagnostics if item.ok)
        if successful == len(diagnostics) and successful:
            status = "ok"
        elif successful:
            status = "partial"
        else:
            status = "unavailable"

        return GalicianaOCRReport(
            status=status,
            queries=queries,
            diagnostics=diagnostics,
            mentions=mentions,
            findings=_aggregate_findings(mentions),
            chronology=_build_chronology(mentions),
            total_unique=len(mentions),
            note=(
                "Los hechos se extraen exclusivamente de los fragmentos OCR y enlaces "
                "devueltos por Galiciana. Los datos previos del usuario solo se usan "
                "para puntuar y separar posibles homónimos. La lectura integral de cada "
                "página se hará mediante leer_pagina_galiciana cuando el visor exponga "
                "texto OCR recuperable."
            ),
        )

    async def read_page(self, page_url: str) -> dict[str, Any]:
        """Open one Galiciana viewer page and extract visible text/media links."""
        page_url = _safe_url(page_url)
        async with self._client() as client:
            response = await client.get(page_url, headers={"Referer": SEARCH_URL})
            response.raise_for_status()
            response, solved = await _resolve_antibot(client, response)

        soup = BeautifulSoup(response.text, "html.parser")
        for element in soup(["script", "style", "noscript"]):
            element.decompose()

        targeted_texts: list[str] = []
        selectors = (
            "[id*='ocr' i]",
            "[class*='ocr' i]",
            "[id*='transcrip' i]",
            "[class*='transcrip' i]",
            "[id*='texto' i]",
            "[class*='texto' i]",
        )
        seen_texts: set[str] = set()
        for selector in selectors:
            for element in soup.select(selector):
                value = _compact(element.get_text(" ", strip=True))
                if len(value) >= 30 and value not in seen_texts:
                    seen_texts.add(value)
                    targeted_texts.append(value)

        images = []
        for image in soup.select("img[src]"):
            source = urljoin(str(response.url), image.get("src", ""))
            if source and source not in images:
                images.append(source)

        documents = []
        for anchor in soup.select("a[href]"):
            href = urljoin(str(response.url), anchor.get("href", ""))
            label = _normalise(anchor.get_text(" ", strip=True))
            if href.lower().endswith(".pdf") or "pdf" in label:
                if href not in documents:
                    documents.append(href)

        visible_text = _compact(soup.get_text(" ", strip=True))
        return {
            "estado": "ok",
            "url": str(response.url),
            "anti_bot_resuelto": solved,
            "textos_ocr_posibles": targeted_texts[:20],
            "texto_visible": visible_text[:30000],
            "imagenes": images[:100],
            "documentos": documents[:30],
            "nota": (
                "Si textos_ocr_posibles está vacío, el visor solo ha expuesto la "
                "imagen y será necesaria una fase posterior de lectura visual/OCR."
            ),
        }
