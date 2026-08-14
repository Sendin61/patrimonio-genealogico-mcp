from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class AIProviderStatus:
    available: bool
    provider: str
    base_url: str
    model: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AIProvider(Protocol):
    """Provider contract used by the local research orchestrator.

    Essential ROB workflows must not require a paid remote provider. Implementations
    may be local (llama.cpp/Ollama) or explicitly optional remote providers later.
    """

    async def status(self) -> AIProviderStatus: ...

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        json_schema: dict[str, Any] | None = None,
    ) -> str: ...


async def chat_json(
    provider: AIProvider,
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    json_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = await provider.chat(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        json_schema=json_schema,
    )
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Local AI did not return valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Local AI JSON response must be an object")
    return value
