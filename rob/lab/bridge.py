from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class BridgeCommandType(StrEnum):
    PING = "ping"
    FAMILYSEARCH_FETCH = "familysearch_fetch"
    FULLTEXT_SEARCH = "fulltext_search"
    RECORD_SEARCH = "record_search"
    TREE_PERSON = "tree_person"
    OCR_PAGE = "ocr_page"
    OCR_PAGES = "ocr_pages"
    STRUCTURED_OCR = "structured_ocr"
    IMAGE_METADATA = "image_metadata"


@dataclass(slots=True)
class BridgeCommand:
    type: BridgeCommandType
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["type"] = self.type.value
        return value


@dataclass(slots=True)
class BridgeResult:
    command_id: str
    ok: bool
    payload: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    completed_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class InMemoryBridgeQueue:
    """Small first-step queue for the local app/extension handshake.

    Persistence and retry semantics belong in the later SQLite-backed bridge store.
    This object intentionally contains no FamilySearch authentication material.
    """

    def __init__(self) -> None:
        self._pending: list[BridgeCommand] = []
        self._results: dict[str, BridgeResult] = {}
        self.last_extension_seen_at: float | None = None

    def enqueue(self, command: BridgeCommand) -> BridgeCommand:
        self._pending.append(command)
        return command

    def next_command(self) -> BridgeCommand | None:
        self.last_extension_seen_at = time.time()
        return self._pending.pop(0) if self._pending else None

    def complete(self, result: BridgeResult) -> None:
        self.last_extension_seen_at = time.time()
        self._results[result.command_id] = result

    def result(self, command_id: str) -> BridgeResult | None:
        return self._results.get(command_id)

    @property
    def connected(self) -> bool:
        return bool(
            self.last_extension_seen_at
            and time.time() - self.last_extension_seen_at < 15
        )
