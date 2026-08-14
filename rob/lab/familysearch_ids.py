from __future__ import annotations

import re


_ALPHABET = "M9S3Q7W4HCZ8D6XFNJVK2LGP5RTYB1"
_BASE = len(_ALPHABET)


def _encode_integer(value: int, minimum_digits: int = 1) -> str:
    if value < 0:
        raise ValueError("Identifier components must be non-negative")
    output = ""
    while value > 0:
        output += _ALPHABET[value % _BASE]
        value //= _BASE
    while len(output) < minimum_digits:
        output += _ALPHABET[0]
    return output[::-1]


def _separate(value: str) -> str:
    position = 4
    while position < len(value):
        value = value[:position] + "-" + value[position:]
        position += 5
    return value


def familysearch_identifier_to_ark(identifier: str) -> str:
    """Encode FamilySearch APID/DGS image identifiers as an image ARK.

    This is a clean-room Python port of the identifier transformation observable in
    FamilySearch's delivered browser JavaScript. It lets ROB address neighbouring DGS
    images without automating the viewer UI.
    """

    value = str(identifier or "").strip()
    match = re.fullmatch(r"DGS-(\d{9})_(\d{5})", value)
    if match:
        dgs_encoded = _encode_integer(int(match.group(1)))
        image_encoded = _encode_integer(int(match.group(2)))
        dgs_length = _encode_integer(len(dgs_encoded))
        return "3:2:" + _separate(dgs_length + dgs_encoded + image_encoded)

    parts = value.split("-")
    if len(parts) < 4:
        raise ValueError(f"Invalid FamilySearch APID: {identifier}")
    parts = parts[1:-1]
    if len(parts) < 2 or not all(part.isdigit() for part in parts):
        raise ValueError(f"Invalid FamilySearch APID: {identifier}")
    encoded = [_encode_integer(int(part)) for part in parts]
    first_len = _encode_integer(len(encoded[0]))
    second_len = _encode_integer(len(encoded[1]))
    encoded.insert(0, first_len + second_len)
    return "3:1:" + _separate("".join(encoded))


def dgs_image_to_ark(dgs: str, image_number: int) -> str:
    digits = re.sub(r"\D", "", str(dgs or ""))
    if not digits or len(digits) > 9:
        raise ValueError(f"Invalid DGS: {dgs}")
    if image_number <= 0 or image_number > 99999:
        raise ValueError(f"Invalid DGS image number: {image_number}")
    return familysearch_identifier_to_ark(
        f"DGS-{int(digits):09d}_{int(image_number):05d}"
    )


def parse_fs_image_id(value: str) -> tuple[str, int]:
    match = re.fullmatch(r"(\d{9})_(\d{5})", str(value or "").strip())
    if not match:
        raise ValueError(f"Invalid FS_IMAGE_ID: {value}")
    return match.group(1), int(match.group(2))
