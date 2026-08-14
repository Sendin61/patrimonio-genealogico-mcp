from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", text)


def _bigrams(value: str) -> set[str]:
    value = "^" + _norm(value) + "$"
    return {value[i:i + 2] for i in range(max(0, len(value) - 1))}


def _dice(a: str, b: str) -> float:
    left = _bigrams(a)
    right = _bigrams(b)
    if not left or not right:
        return 0.0
    return 2.0 * len(left & right) / (len(left) + len(right))


@dataclass(frozen=True, slots=True)
class OCRObservation:
    """One literal OCR reading at one documentary position.

    `raw` is immutable evidence.  No canonical spelling is stored here.
    """

    raw: str
    image_id: str | None = None
    image_number: int | None = None
    line_id: str | None = None
    token_id: str | None = None
    role_hint: str | None = None
    context_before: str = ""
    context_after: str = ""
    source_layer: str = "familysearch_ocr"


@dataclass(slots=True)
class OCRVariantFamily:
    """A latent family of readings that may refer to the same underlying text/entity.

    This is deliberately NOT a corrected word.  A family can remain unresolved forever.
    Multiple recurrent variants are first-class evidence.
    """

    id: str
    observations: list[OCRObservation] = field(default_factory=list)
    candidate_readings: dict[str, float] = field(default_factory=dict)
    visual_status: str = "not_checked"
    visual_notes: list[str] = field(default_factory=list)

    def counts(self) -> Counter[str]:
        return Counter(obs.raw for obs in self.observations if obs.raw)

    def normalized_counts(self) -> Counter[str]:
        return Counter(_norm(obs.raw) for obs in self.observations if _norm(obs.raw))

    def recurrent_variants(self, minimum: int = 2) -> list[tuple[str, int]]:
        return [(value, count) for value, count in self.counts().most_common() if count >= minimum]

    def entropy(self) -> float:
        counts = list(self.counts().values())
        total = sum(counts)
        if total <= 1:
            return 0.0
        return -sum((count / total) * math.log2(count / total) for count in counts)

    def instability(self) -> float:
        """0 = one stable reading, 1 ~= highly fragmented readings.

        This is descriptive only.  It must never be used as proof that a candidate is wrong.
        """
        counts = self.counts()
        total = sum(counts.values())
        if not total:
            return 0.0
        dominant = max(counts.values())
        diversity = min(1.0, max(0, len(counts) - 1) / max(1, total - 1))
        return min(1.0, 0.65 * (1.0 - dominant / total) + 0.35 * diversity)

    def needs_visual_verification(self, *, high_impact: bool = False) -> bool:
        counts = self.counts()
        if not counts:
            return high_impact
        if high_impact and len(counts) > 1:
            return True
        if len(counts) >= 4:
            return True
        if self.instability() >= 0.38:
            return True
        if self.candidate_readings:
            ordered = sorted(self.candidate_readings.values(), reverse=True)
            if len(ordered) >= 2 and ordered[0] - ordered[1] < 0.18:
                return True
        return False

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "observations": [
                {
                    "raw": obs.raw,
                    "image_id": obs.image_id,
                    "image_number": obs.image_number,
                    "line_id": obs.line_id,
                    "token_id": obs.token_id,
                    "role_hint": obs.role_hint,
                    "context_before": obs.context_before,
                    "context_after": obs.context_after,
                    "source_layer": obs.source_layer,
                }
                for obs in self.observations
            ],
            "variant_counts": dict(self.counts()),
            "recurrent_variants": self.recurrent_variants(),
            "entropy": self.entropy(),
            "instability": self.instability(),
            "candidate_readings": dict(self.candidate_readings),
            "visual_status": self.visual_status,
            "visual_notes": list(self.visual_notes),
        }


def cluster_observations(
    observations: Iterable[OCRObservation],
    *,
    lexical_threshold: float = 0.58,
    contextual_links: dict[tuple[int, int], float] | None = None,
) -> list[OCRVariantFamily]:
    """Build conservative families without assuming lexical similarity is sufficient.

    `contextual_links[(i,j)]` may encode evidence from repeated relatives, documentary role,
    same writer/volume, neighbouring clauses, etc.  This allows extremely distant OCR forms
    such as `Bea` and `Varela` to enter the same family when documentary context is strong.
    """

    items = list(observations)
    links = contextual_links or {}
    parent = list(range(len(items)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        a = find(left)
        b = find(right)
        if a != b:
            parent[b] = a

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            lexical = _dice(items[i].raw, items[j].raw)
            contextual = max(
                float(links.get((i, j), 0.0)),
                float(links.get((j, i), 0.0)),
            )
            same_role = bool(items[i].role_hint and items[i].role_hint == items[j].role_hint)
            # Strong context may override almost zero spelling similarity.  Conversely,
            # spelling similarity alone needs to be fairly convincing.
            if contextual >= 0.82 or lexical >= lexical_threshold or (same_role and contextual >= 0.62):
                union(i, j)

    grouped: dict[int, list[OCRObservation]] = defaultdict(list)
    for index, observation in enumerate(items):
        grouped[find(index)].append(observation)

    families: list[OCRVariantFamily] = []
    for number, group in enumerate(grouped.values(), start=1):
        families.append(OCRVariantFamily(id=f"ocr_family_{number:04d}", observations=group))
    return families
