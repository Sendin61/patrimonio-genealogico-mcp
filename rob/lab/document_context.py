from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from .models import DocumentContext, PageContext


PageLoader = Callable[[int], Awaitable[PageContext | None]]
ContinuityDecider = Callable[[PageContext, PageContext, str], Awaitable[tuple[bool, str]]]


class DocumentContextBuilder:
    """Build a coherent multi-page document around a matching image.

    The builder deliberately separates retrieval from interpretation. The caller provides
    a page loader and a continuity decider. The default initial window is +/-3 images,
    then the context may expand until a boundary is detected or the safety limit is hit.
    Initial page retrieval is concurrent so a seven-page context does not feel like seven
    serial viewer navigations.
    """

    def __init__(
        self,
        page_loader: PageLoader,
        continuity_decider: ContinuityDecider,
        *,
        initial_radius: int = 3,
        max_extra_pages_each_side: int = 12,
    ) -> None:
        if initial_radius < 0:
            raise ValueError("initial_radius cannot be negative")
        if max_extra_pages_each_side < 0:
            raise ValueError("max_extra_pages_each_side cannot be negative")
        self.page_loader = page_loader
        self.continuity_decider = continuity_decider
        self.initial_radius = initial_radius
        self.max_extra_pages_each_side = max_extra_pages_each_side

    async def _load_range(self, start: int, end: int) -> dict[int, PageContext]:
        numbers = list(range(max(1, start), max(1, end) + 1))
        loaded = await asyncio.gather(*(self.page_loader(number) for number in numbers))
        return {
            number: page
            for number, page in zip(numbers, loaded, strict=True)
            if page is not None
        }

    async def build(self, center_image: int) -> DocumentContext:
        if center_image < 1:
            raise ValueError("center_image must be positive")

        pages = await self._load_range(
            center_image - self.initial_radius,
            center_image + self.initial_radius,
        )
        if center_image not in pages:
            center = await self.page_loader(center_image)
            if center is None:
                raise LookupError(f"central image {center_image} is unavailable")
            pages[center_image] = center

        reasons: list[str] = []
        low = min(pages)
        high = max(pages)

        backward_expansions = 0
        while low > 1 and backward_expansions < self.max_extra_pages_each_side:
            current = pages.get(low)
            candidate_number = low - 1
            candidate = await self.page_loader(candidate_number)
            if current is None or candidate is None:
                reasons.append(f"backward boundary near image {low}: page unavailable")
                break
            continues, reason = await self.continuity_decider(candidate, current, "backward")
            reasons.append(reason)
            if not continues:
                break
            pages[candidate_number] = candidate
            low = candidate_number
            backward_expansions += 1

        forward_expansions = 0
        while forward_expansions < self.max_extra_pages_each_side:
            current = pages.get(high)
            candidate_number = high + 1
            candidate = await self.page_loader(candidate_number)
            if current is None or candidate is None:
                reasons.append(f"forward boundary near image {high}: page unavailable")
                break
            continues, reason = await self.continuity_decider(current, candidate, "forward")
            reasons.append(reason)
            if not continues:
                break
            pages[candidate_number] = candidate
            high = candidate_number
            forward_expansions += 1

        ordered = [pages[number] for number in sorted(pages)]
        return DocumentContext(
            center_image=center_image,
            pages=ordered,
            estimated_start_image=min(pages),
            estimated_end_image=max(pages),
            reasons=reasons,
        )
