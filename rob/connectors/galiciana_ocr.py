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
from xml.etree import ElementTree as ET

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
    attempts: int = 0
    discarded_out_of_range: int = 0
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


def _within_year_range(date: str | None, query: GenealogyQuery) -> bool:
    """Apply the user's date interval as a real filter, not only a score hint."""
    year = _extract_year(date)
    if year is None:
        return True
    if query.year_from is not None and year < query.year_from:
        return False
    if query.year_to is not None and year > query.year_to:
        return False
    return True


def _is_exact_full_phrase_query(text_query: str, query: GenealogyQuery) -> bool:
    """True only for a quoted phrase containing the full canonical name."""
    stripped = text_query.strip()
    if not (stripped.startswith('"') and stripped.endswith('"')):
        return False
    if stripped.count('"') != 2 or "?" in stripped or "*" in stripped:
        return False

    query_tokens = re.findall(r"[\wÀ-ÿ'-]+", _normalise(stripped.strip('"')))
    canonical_tokens = re.findall(r"[\wÀ-ÿ'-]+", _normalise(query.name))
    return bool(canonical_tokens) and sorted(query_tokens) == sorted(canonical_tokens)


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



def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _xml_href(element: ET.Element) -> str | None:
    for key, value in element.attrib.items():
        if key.casefold() == "href" or key.casefold().endswith("}href"):
            return value.strip() or None
    return None


def _safe_discovered_url(url: str, *, base_url: str) -> str | None:
    absolute = urljoin(base_url, url.strip())
    parsed = urlparse(absolute)
    hostname = (parsed.hostname or "").casefold()
    if hostname != ALLOWED_HOST and not hostname.endswith(".galiciana.gal"):
        return None
    if parsed.scheme == "http":
        parsed = parsed._replace(scheme="https")
        absolute = parsed.geturl()
    elif parsed.scheme != "https":
        return None
    return absolute


def _extract_alto_text(xml_text: str) -> str:
    """Extract reading-order text from an ALTO XML page."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""

    lines: list[str] = []
    for element in root.iter():
        if _local_name(element.tag) != "textline":
            continue
        words: list[str] = []
        for descendant in element.iter():
            if _local_name(descendant.tag) != "string":
                continue
            content = descendant.attrib.get("CONTENT") or descendant.attrib.get("content")
            if content:
                words.append(content)
        line = _compact(" ".join(words))
        if line:
            lines.append(line)

    if not lines:
        words = []
        for element in root.iter():
            if _local_name(element.tag) == "string":
                content = element.attrib.get("CONTENT") or element.attrib.get("content")
                if content:
                    words.append(content)
        return _compact(" ".join(words))

    return "\n".join(lines).strip()


def _viewer_page_ids(soup: BeautifulSoup) -> list[str]:
    output: list[str] = []
    for image in soup.select('img[src*="object-miniature.do?id="]'):
        image_id = _query_value(urljoin(BASE_URL, image.get("src", "")), "id")
        if image_id and image_id not in output:
            output.append(image_id)
    if not output:
        for anchor in soup.select('a[id^="img"]'):
            match = re.fullmatch(r"img(\d+)(?:-icon)?", anchor.get("id", ""))
            if match and match.group(1) not in output:
                output.append(match.group(1))
    return output


def _parse_mets_document(
    xml_text: str,
    *,
    base_url: str,
    target_image_id: str | None,
    target_index: int | None,
) -> dict[str, Any]:
    """Parse METS and identify the image/ALTO files for one viewer page."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        return {
            "ok": False,
            "error": f"METS XML no válido: {exc}",
            "images": [],
            "ocr": [],
            "pdfs": [],
            "selected_image": None,
            "selected_ocr": None,
        }

    files: list[dict[str, Any]] = []
    files_by_id: dict[str, dict[str, Any]] = {}
    for group in root.iter():
        if _local_name(group.tag) != "filegrp":
            continue
        group_use = group.attrib.get("USE", "")
        for file_element in group.iter():
            if _local_name(file_element.tag) != "file":
                continue
            file_id = file_element.attrib.get("ID", "")
            mime = file_element.attrib.get("MIMETYPE", "")
            href = None
            for child in file_element.iter():
                if _local_name(child.tag) == "flocat":
                    href = _xml_href(child)
                    if href:
                        break
            if not href:
                continue
            absolute = _safe_discovered_url(href, base_url=base_url)
            if not absolute:
                continue
            lowered = urlparse(absolute).path.casefold()
            descriptor = _normalise(" ".join((group_use, mime, file_id, lowered)))
            category = "other"
            if "ocr" in descriptor or "alto" in descriptor:
                category = "ocr"
            elif "pdf" in descriptor or lowered.endswith(".pdf"):
                category = "pdf"
            elif mime.casefold().startswith("image/") or lowered.endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff", ".jp2")):
                category = "image"
            elif mime.casefold() in {"text/xml", "application/xml"} or lowered.endswith(".xml"):
                category = "ocr"

            item = {
                "id": file_id,
                "url": absolute,
                "mime": mime or None,
                "use": group_use or None,
                "category": category,
            }
            files.append(item)
            if file_id:
                files_by_id[file_id] = item

    page_groups: list[list[str]] = []
    for div in root.iter():
        if _local_name(div.tag) != "div":
            continue
        refs: list[str] = []
        child_div_has_refs = False
        for child in list(div):
            if _local_name(child.tag) == "fptr":
                file_id = child.attrib.get("FILEID", "")
                if file_id:
                    refs.append(file_id)
            elif _local_name(child.tag) == "div":
                if any(_local_name(grand.tag) == "fptr" for grand in list(child)):
                    child_div_has_refs = True
        div_type = _normalise(div.attrib.get("TYPE", ""))
        if refs and ("page" in div_type or "pagina" in div_type or not child_div_has_refs):
            page_groups.append(refs)

    images = [item for item in files if item["category"] == "image"]
    ocr_files = [item for item in files if item["category"] == "ocr"]
    pdfs = [item for item in files if item["category"] == "pdf"]

    selected_image = None
    if target_image_id:
        for item in images:
            if target_image_id in item["id"] or target_image_id in item["url"]:
                selected_image = item
                break
    if selected_image is None and target_index is not None and 0 <= target_index < len(images):
        selected_image = images[target_index]

    selected_group: list[str] | None = None
    if selected_image and selected_image["id"]:
        for refs in page_groups:
            if selected_image["id"] in refs:
                selected_group = refs
                break
    if selected_group is None and target_image_id:
        for refs in page_groups:
            if any(target_image_id in ref for ref in refs):
                selected_group = refs
                break
    if selected_group is None and target_index is not None and 0 <= target_index < len(page_groups):
        selected_group = page_groups[target_index]

    selected_ocr = None
    if selected_group:
        for ref in selected_group:
            item = files_by_id.get(ref)
            if item and item["category"] == "ocr":
                selected_ocr = item
                break
    if selected_ocr is None and target_image_id:
        for item in ocr_files:
            if target_image_id in item["id"] or target_image_id in item["url"]:
                selected_ocr = item
                break
    if selected_ocr is None and target_index is not None and 0 <= target_index < len(ocr_files):
        selected_ocr = ocr_files[target_index]
    if selected_ocr is None and len(ocr_files) == 1:
        selected_ocr = ocr_files[0]

    return {
        "ok": True,
        "error": None,
        "images": images,
        "ocr": ocr_files,
        "pdfs": pdfs,
        "page_groups": page_groups,
        "selected_image": selected_image,
        "selected_ocr": selected_ocr,
    }

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


def _string_assignment_history(script: str) -> list[tuple[str, str, int]]:
    """Preserve every literal assignment and its position.

    The remote protection reuses short variable names. Keeping only the last
    value can therefore replace the encoded session token with an unrelated
    later assignment.
    """
    history: list[tuple[str, str, int]] = []
    pattern = re.compile(
        r"(?:\bvar\s+)?([A-Za-z_$][\w$]*)\s*=\s*"
        r"(?P<quote>['\"])(?P<value>(?:\\.|(?!\2).)*?)(?P=quote)\s*;",
        flags=re.DOTALL,
    )
    for match in pattern.finditer(script):
        history.append(
            (
                match.group(1),
                _decode_js_string(match.group("value"), match.group("quote")),
                match.start(),
            )
        )
    return history


def _string_assignments(script: str) -> dict[str, str]:
    """Compatibility view containing the final value of each variable."""
    assignments: dict[str, str] = {}
    for name, value, _ in _string_assignment_history(script):
        assignments[name] = value
    return assignments


def _assignment_before(
    history: list[tuple[str, str, int]],
    variable: str,
    position: int,
) -> str:
    values = [value for name, value, offset in history if name == variable and offset < position]
    return values[-1] if values else ""


def _decode_base64_text(value: str) -> str | None:
    """Decode the protection's Base64 token without assuming one alphabet."""
    candidate = value.strip().replace(r"\/", "/")
    if not candidate:
        return None
    variants = [candidate, candidate.replace("-", "+").replace("_", "/")]
    for variant in variants:
        padded = variant + "=" * (-len(variant) % 4)
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                raw = decoder(padded)
            except Exception:
                continue
            for encoding in ("utf-8", "latin-1"):
                try:
                    text = raw.decode(encoding)
                except UnicodeDecodeError:
                    continue
                readable = sum(
                    char.isprintable() or char in "\r\n\t" for char in text
                )
                if text and readable / len(text) >= 0.95:
                    return text
    return None


def _find_fwb_payload_fallback(*texts: str) -> str | None:
    """Find an encoded raw Galiciana request for any protected endpoint."""
    candidates: list[str] = []
    for text in texts:
        candidates.extend(
            re.findall(
                r"(?<![A-Za-z0-9+/=_-])([A-Za-z0-9+/_-]{48,}={0,2})(?![A-Za-z0-9+/=_-])",
                text,
            )
        )

    for candidate in sorted(set(candidates), key=len, reverse=True):
        decoded = _decode_base64_text(candidate)
        if not decoded:
            continue
        if re.search(r"^(?:GET|POST)\s+/\S*\.do(?:\?\S*)?\s+HTTP/1\.[01]", decoded):
            if "Host: biblioteca.galiciana.gal" in decoded:
                return candidate
    return None


def _cookie_candidate_from_history(
    history: list[tuple[str, str, int]],
    *,
    before: int,
) -> str | None:
    """Recover a session token when obfuscated variable reuse breaks direct lookup."""
    ranked: list[tuple[int, int, str]] = []
    for _, encoded, position in history:
        if position >= before:
            continue
        decoded = _decode_base64_text(encoded)
        if not decoded or "HTTP/" in decoded or any(char.isspace() for char in decoded):
            continue
        score = 0
        if re.fullmatch(r"[A-Fa-f0-9]{16,128}", decoded):
            score = 100
        elif re.fullmatch(r"[A-Za-z0-9._:-]{12,160}", decoded):
            score = 70
        elif 8 <= len(decoded) <= 200:
            score = 40
        if score:
            ranked.append((score, position, decoded))
    if not ranked:
        return None
    ranked.sort(reverse=True)
    return ranked[0][2]


def extract_antibot_challenge(html: str) -> tuple[str, str, str, str]:
    """Return endpoint, query-session name/value and fwb_dat payload."""
    soup = BeautifulSoup(html, "html.parser")
    packed = "\n".join(
        element.get_text("\n", strip=False)
        for element in soup.find_all("script")
        if "eval(function(p" in element.get_text(" ", strip=False)
    )
    if not packed:
        raise ValueError("La respuesta no contiene el desafío anti-bot esperado.")

    script = unpack_dean_edwards_packer(packed)
    history = _string_assignment_history(script)
    assignments = _string_assignments(script)

    payload_match = re.search(
        r"['\"]fwb_dat=['\"]\s*\+\s*"
        r"(?:['\"](?P<literal>[A-Za-z0-9+/_=-]+)['\"]|(?P<variable>[A-Za-z_$][\w$]*))",
        script,
    )
    payload = ""
    if payload_match is not None:
        if payload_match.group("literal"):
            payload = payload_match.group("literal") or ""
        else:
            payload = _assignment_before(
                history,
                payload_match.group("variable") or "",
                payload_match.start(),
            ) or assignments.get(payload_match.group("variable") or "", "")
    if not payload:
        payload = _find_fwb_payload_fallback(script, packed) or ""
    if not payload:
        raise ValueError("No se pudo extraer fwb_dat del desafío anti-bot.")

    decoded_request = _decode_base64_text(payload) or ""
    request_path_match = re.search(
        r"^(?:GET|POST)\s+(?P<path>/\S*?\.do(?:\?\S*)?)\s+HTTP/1\.[01]",
        decoded_request,
    )

    endpoint = ""
    if request_path_match:
        endpoint = request_path_match.group("path")
    if not endpoint:
        endpoint = next(
            (
                value
                for _, value, _ in history
                if value.startswith("/") and ".do" in value
            ),
            "/es/consulta/resultados_ocr.do",
        )

    cookie_match = re.search(
        r"\+=\s*['\"]\?['\"]\s*\+\s*([A-Za-z_$][\w$]*)\s*"
        r"\+\s*['\"]=['\"]\s*\+\s*[A-Za-z_$][\w$]*\("
        r"([A-Za-z_$][\w$]*)\)",
        script,
    )
    cookie_position = cookie_match.start() if cookie_match else len(script)

    cookie_name = ""
    encoded_cookie_value = ""
    if cookie_match is not None:
        cookie_name = _assignment_before(history, cookie_match.group(1), cookie_position)
        encoded_cookie_value = _assignment_before(
            history,
            cookie_match.group(2),
            cookie_position,
        )

    if not cookie_name:
        cookie_name = next(
            (
                value
                for _, value, _ in history
                if re.fullmatch(r"cookiesession\d+", value, re.IGNORECASE)
            ),
            "",
        )
    if not cookie_name:
        direct_cookie = re.search(r"cookiesession\d+", script, re.IGNORECASE)
        cookie_name = direct_cookie.group(0) if direct_cookie else ""
    if not cookie_name:
        raise ValueError("No se pudo extraer el parámetro de sesión anti-bot.")

    cookie_value = _decode_base64_text(encoded_cookie_value) if encoded_cookie_value else None
    if not cookie_value or "HTTP/" in cookie_value or any(char.isspace() for char in cookie_value):
        cookie_value = _cookie_candidate_from_history(history, before=cookie_position)
    if not cookie_value:
        raise ValueError("No se pudo decodificar la sesión anti-bot.")

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


def _mention_key(mention: GalicianaOCRMention) -> tuple[str, str]:
    """One physical Galiciana page is one mention, regardless of query/snippet."""
    return (
        mention.record_id or mention.title,
        mention.image_id or mention.page_url,
    )


def _merge_mentions(
    previous: GalicianaOCRMention,
    current: GalicianaOCRMention,
    query: GenealogyQuery,
) -> GalicianaOCRMention:
    snippets: list[str] = []
    for value in [*previous.snippets, *current.snippets]:
        normalised = _normalise(value)
        if value and normalised not in {_normalise(item) for item in snippets}:
            snippets.append(value)

    score, reasons = _score_mention(
        " ".join(snippets),
        previous.date or current.date,
        query,
    )
    best = current if current.score > previous.score else previous
    return GalicianaOCRMention(
        record_id=best.record_id,
        title=best.title,
        parent_publication=best.parent_publication,
        date=best.date,
        page=best.page,
        record_url=best.record_url,
        page_url=best.page_url,
        digital_copy_url=best.digital_copy_url,
        pdf_url=best.pdf_url,
        path=best.path,
        image_id=best.image_id,
        snippets=snippets,
        matched_query=best.matched_query,
        score=score,
        score_reasons=reasons,
        interpretations=interpret_snippets(snippets),
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
        retries: int = 2,
    ) -> tuple[list[GalicianaOCRMention], OCRSearchDiagnostic]:
        diagnostic = OCRSearchDiagnostic(query=text_query, ok=False)
        last_exc: Exception | None = None

        for attempt in range(1, max(1, retries) + 1):
            diagnostic.attempts = attempt
            try:
                # Seed a normal browser-like session before submitting the form.
                try:
                    await client.get(SEARCH_REFERER)
                except httpx.HTTPError:
                    pass

                response = await client.post(
                    SEARCH_URL,
                    data={"general_ocr": "on", "busq_general": text_query},
                    headers={"Origin": BASE_URL, "Referer": SEARCH_REFERER},
                )
                response.raise_for_status()
                response, solved = await _resolve_antibot(client, response)
                diagnostic.challenge_solved = diagnostic.challenge_solved or solved

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

                    for mention in parsed.mentions:
                        if _within_year_range(mention.date, genealogy_query):
                            all_mentions.append(mention)
                        else:
                            diagnostic.discarded_out_of_range += 1

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
                    diagnostic.challenge_solved = (
                        diagnostic.challenge_solved or next_solved
                    )
                    current_url = str(next_response.url)
                    current_html = next_response.text

                diagnostic.ok = True
                diagnostic.mentions_parsed = len(all_mentions[:maximum_results])
                diagnostic.error_type = None
                diagnostic.error = None
                return all_mentions[:maximum_results], diagnostic

            except Exception as exc:
                last_exc = exc
                diagnostic.error_type = type(exc).__name__
                diagnostic.error = str(exc).strip() or repr(exc)
                if attempt < retries:
                    await asyncio.sleep(0.6 * attempt)

        if last_exc is not None:
            diagnostic.error_type = type(last_exc).__name__
            diagnostic.error = str(last_exc).strip() or repr(last_exc)
        return [], diagnostic

    async def investigate(
        self,
        query: GenealogyQuery,
        *,
        maximum_queries: int = 6,
        maximum_results: int = 120,
        maximum_pages_per_query: int = 5,
    ) -> GalicianaOCRReport:
        planned_queries = build_ocr_queries(query, maximum=maximum_queries)
        executed_queries: list[str] = []
        diagnostics: list[OCRSearchDiagnostic] = []
        unique: dict[tuple[str, str], GalicianaOCRMention] = {}

        # Once a full quoted name returns a substantial result set, broad
        # surname/wildcard searches create more homonyms than useful evidence.
        exact_hits = 0

        for index, text_query in enumerate(planned_queries):
            is_exact = _is_exact_full_phrase_query(text_query, query)
            if not is_exact and exact_hits >= 5:
                continue

            remaining = max(1, maximum_results - len(unique))
            async with self._client() as client:
                mentions, diagnostic = await self._search_one(
                    client,
                    text_query=text_query,
                    genealogy_query=query,
                    maximum_results=remaining,
                    maximum_pages=maximum_pages_per_query,
                )

            executed_queries.append(text_query)
            diagnostics.append(diagnostic)

            if diagnostic.ok and is_exact:
                exact_hits += len(mentions)

            for mention in mentions:
                key = _mention_key(mention)
                previous = unique.get(key)
                if previous is None:
                    unique[key] = mention
                else:
                    unique[key] = _merge_mentions(previous, mention, query)

            if len(unique) >= maximum_results:
                break
            if index < len(planned_queries) - 1:
                await asyncio.sleep(0.35)

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
            queries=executed_queries,
            diagnostics=diagnostics,
            mentions=mentions,
            findings=_aggregate_findings(mentions),
            chronology=_build_chronology(mentions),
            total_unique=len(mentions),
            note=(
                "Cada página física se devuelve una sola vez aunque aparezca en "
                "varias consultas. El intervalo cronológico se aplica como filtro "
                "real. Las búsquedas amplias solo se ejecutan cuando las frases "
                "exactas no producen suficientes resultados. Los hechos proceden "
                "exclusivamente del OCR y de los documentos de Galiciana."
            ),
        )

    async def read_page(self, page_url: str) -> dict[str, Any]:
        """Open a Galiciana page and recover its METS/ALTO text when exposed."""
        page_url = _safe_url(page_url)
        target_image_id = _query_value(page_url, "idImagen")
        recovery_errors: list[str] = []

        async with self._client() as client:
            # The viewer and METS download are more likely to challenge a cold
            # server session than the search form. Prime the same session first.
            for seed_url in (f"{BASE_URL}/es/inicio/inicio.do", SEARCH_REFERER):
                try:
                    seed_response = await client.get(seed_url)
                    seed_response.raise_for_status()
                    if _is_antibot_page(seed_response.text):
                        await _resolve_antibot(client, seed_response)
                except Exception as exc:
                    recovery_errors.append(
                        f"sesión inicial {seed_url}: {type(exc).__name__}: "
                        f"{str(exc).strip() or repr(exc)}"
                    )

            response = None
            solved = False
            last_viewer_error: Exception | None = None
            for attempt in range(1, 4):
                try:
                    candidate_response = await client.get(
                        page_url,
                        headers={"Referer": SEARCH_URL},
                    )
                    candidate_response.raise_for_status()
                    candidate_response, attempt_solved = await _resolve_antibot(
                        client, candidate_response
                    )
                    solved = solved or attempt_solved
                    response = candidate_response
                    break
                except Exception as exc:
                    last_viewer_error = exc
                    recovery_errors.append(
                        f"visor intento {attempt}: {type(exc).__name__}: "
                        f"{str(exc).strip() or repr(exc)}"
                    )
                    if attempt < 3:
                        await asyncio.sleep(0.5 * attempt)

            if response is None:
                return {
                    "estado": "unavailable",
                    "lectura_completa": False,
                    "url": page_url,
                    "anti_bot_resuelto": solved,
                    "id_imagen": target_image_id,
                    "texto_ocr": "",
                    "ocr_url": None,
                    "imagen_pagina": None,
                    "mets_url": None,
                    "errores_recuperacion": recovery_errors[-20:],
                    "error": (
                        f"{type(last_viewer_error).__name__}: {last_viewer_error}"
                        if last_viewer_error
                        else "No se pudo abrir el visor."
                    ),
                }

            raw_soup = BeautifulSoup(response.text, "html.parser")
            page_ids = _viewer_page_ids(raw_soup)
            target_index = (
                page_ids.index(target_image_id)
                if target_image_id and target_image_id in page_ids
                else None
            )

            mets_links: list[str] = []
            document_links: list[str] = []
            action_links: list[dict[str, str]] = []
            for anchor in raw_soup.select("a[href]"):
                href = _safe_discovered_url(
                    anchor.get("href", ""),
                    base_url=str(response.url),
                )
                label_raw = _compact(anchor.get_text(" ", strip=True))
                label = _normalise(label_raw)
                if href and label_raw:
                    action_links.append({"label": label_raw, "url": href})
                if href and ("mets" in label or "mets" in href.casefold()):
                    if href not in mets_links:
                        if "mets" in href.casefold():
                            mets_links.insert(0, href)
                        else:
                            mets_links.append(href)
                if href and (
                    href.casefold().endswith(".pdf")
                    or "pdf" in label
                    or "descargar grupo" in label
                ) and href not in document_links:
                    document_links.append(href)

            # Some DIGIBIB templates place download URLs in data-* attributes or scripts.
            for element in raw_soup.find_all(True):
                for attr_value in element.attrs.values():
                    values = attr_value if isinstance(attr_value, list) else [attr_value]
                    for value in values:
                        if not isinstance(value, str) or "mets" not in value.casefold():
                            continue
                        candidate = _safe_discovered_url(value, base_url=str(response.url))
                        if candidate and candidate not in mets_links:
                            mets_links.append(candidate)
            for match in re.finditer(r"[\"']([^\"']*mets[^\"']*)[\"']", response.text, re.I):
                candidate = _safe_discovered_url(match.group(1), base_url=str(response.url))
                if candidate and candidate not in mets_links:
                    mets_links.append(candidate)

            mets_url = None
            mets_summary: dict[str, Any] | None = None
            alto_text = ""
            alto_url = None
            page_image_url = None
            discovered_images: list[str] = []
            discovered_ocr: list[str] = []
            discovered_pdfs: list[str] = []

            for candidate in mets_links[:5]:
                try:
                    mets_response = await client.get(
                        candidate,
                        headers={"Referer": str(response.url)},
                    )
                    mets_response.raise_for_status()
                    mets_response, _ = await _resolve_antibot(client, mets_response)
                    parsed_mets = _parse_mets_document(
                        mets_response.text,
                        base_url=str(mets_response.url),
                        target_image_id=target_image_id,
                        target_index=target_index,
                    )
                    if not parsed_mets.get("ok"):
                        recovery_errors.append(
                            f"METS {candidate}: {parsed_mets.get('error')}"
                        )
                        continue

                    mets_url = str(mets_response.url)
                    discovered_images = [item["url"] for item in parsed_mets["images"]]
                    discovered_ocr = [item["url"] for item in parsed_mets["ocr"]]
                    discovered_pdfs = [item["url"] for item in parsed_mets["pdfs"]]
                    selected_image = parsed_mets.get("selected_image")
                    selected_ocr = parsed_mets.get("selected_ocr")
                    page_image_url = selected_image["url"] if selected_image else None
                    alto_url = selected_ocr["url"] if selected_ocr else None
                    mets_summary = {
                        "imagenes": len(parsed_mets["images"]),
                        "ocr": len(parsed_mets["ocr"]),
                        "pdfs": len(parsed_mets["pdfs"]),
                        "grupos_paginas": len(parsed_mets.get("page_groups", [])),
                        "indice_pagina_objetivo": target_index,
                    }

                    if alto_url:
                        alto_response = await client.get(
                            alto_url,
                            headers={"Referer": mets_url},
                        )
                        alto_response.raise_for_status()
                        alto_text = _extract_alto_text(alto_response.text)
                    break
                except Exception as exc:
                    recovery_errors.append(
                        f"{candidate}: {type(exc).__name__}: {str(exc).strip() or repr(exc)}"
                    )

        soup = BeautifulSoup(response.text, "html.parser")
        comment_texts: list[str] = []
        for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
            value = _compact(str(comment))
            if len(value) >= 80 and "<" not in value[:20]:
                comment_texts.append(value)

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
        for value in comment_texts:
            if value not in seen_texts:
                targeted_texts.append(value)
                seen_texts.add(value)

        images: list[str] = []
        for image in soup.select("img[src]"):
            source = urljoin(str(response.url), image.get("src", ""))
            if source and source not in images:
                images.append(source)
        for source in discovered_images:
            if source not in images:
                images.append(source)

        documents = list(document_links)
        for source in [*discovered_pdfs, *mets_links]:
            if source not in documents:
                documents.append(source)

        visible_text = _compact(soup.get_text(" ", strip=True))
        complete = bool(alto_text)
        return {
            "estado": "ok" if complete else "partial",
            "lectura_completa": complete,
            "url": str(response.url),
            "anti_bot_resuelto": solved,
            "id_imagen": target_image_id,
            "indice_pagina": target_index,
            "texto_ocr": alto_text[:120000],
            "ocr_origen": "ALTO XML enlazado desde METS" if alto_text else None,
            "ocr_url": alto_url,
            "imagen_pagina": page_image_url,
            "mets_url": mets_url,
            "mets_resumen": mets_summary,
            "textos_ocr_posibles": targeted_texts[:20],
            "texto_visible": visible_text[:30000],
            "imagenes": images[:200],
            "ocr_descubierto": discovered_ocr[:200],
            "documentos": documents[:100],
            "acciones": action_links[:100],
            "errores_recuperacion": recovery_errors[:20],
            "nota": (
                "La lectura completa se considera verificada solo cuando texto_ocr "
                "procede del ALTO XML asociado a la página en el METS. Si queda en "
                "partial, la siguiente vía será descargar la imagen original y aplicar OCR."
            ),
        }
