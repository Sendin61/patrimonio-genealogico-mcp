from __future__ import annotations

from typing import Any

import httpx


class ExaAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _bounded(value: Any, maximum: int) -> str:
    return str(value or "")[:maximum]


class ExaClient:
    """Small, injectable client for the Exa Search and Contents APIs."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 20.0,
        base_url: str = "https://api.exa.ai",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key.strip()
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self.transport = transport

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._api_key:
            raise ExaAPIError("Exa no está configurada.")
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self.transport
            ) as client:
                response = await client.post(
                    f"{self.base_url}{path}",
                    headers={"x-api-key": self._api_key},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            raise ExaAPIError(
                f"Exa respondió HTTP {exc.response.status_code}.",
                status_code=exc.response.status_code,
            ) from None
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ExaAPIError(f"Error de red de Exa: {type(exc).__name__}.") from None
        except ValueError:
            raise ExaAPIError("Exa devolvió JSON no válido.") from None
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ExaAPIError("La respuesta de Exa no contiene una lista results válida.")
        return data

    @staticmethod
    def _diagnostics(data: dict[str, Any]) -> dict[str, Any]:
        return {
            key: data[key]
            for key in ("requestId", "costDollars")
            if key in data and isinstance(data[key], (str, int, float))
        }

    async def search(self, query: str, *, num_results: int) -> dict[str, Any]:
        data = await self._post(
            "/search",
            {"query": query, "type": "auto", "numResults": min(max(num_results, 1), 80)},
        )
        results: list[dict[str, Any]] = []
        for raw in data["results"][:80]:
            if not isinstance(raw, dict) or not str(raw.get("url") or "").strip():
                continue
            results.append(
                {
                    "url": _bounded(raw.get("url"), 4000),
                    "title": _bounded(raw.get("title"), 1000),
                    "author": _bounded(raw.get("author"), 500),
                    "publishedDate": _bounded(raw.get("publishedDate"), 100),
                    "image": _bounded(raw.get("image"), 4000),
                    "score": raw.get("score") if isinstance(raw.get("score"), (int, float)) else None,
                }
            )
        return {"results": results, "diagnostics": self._diagnostics(data)}

    async def contents(
        self,
        urls: list[str],
        *,
        highlight_query: str,
        max_characters: int,
    ) -> dict[str, Any]:
        data = await self._post(
            "/contents",
            {
                "ids": urls[:80],
                "highlights": {
                    "query": highlight_query[:2000],
                    "numSentences": 3,
                    "highlightsPerUrl": 5,
                },
                "text": {"maxCharacters": min(max(max_characters, 1), 8000)},
            },
        )
        results: list[dict[str, Any]] = []
        for raw in data["results"][:80]:
            if not isinstance(raw, dict):
                continue
            url = _bounded(raw.get("url") or raw.get("id"), 4000)
            highlights = raw.get("highlights") or []
            if not isinstance(highlights, list):
                highlights = []
            results.append(
                {
                    "url": url,
                    "title": _bounded(raw.get("title"), 1000),
                    "author": _bounded(raw.get("author"), 500),
                    "publishedDate": _bounded(raw.get("publishedDate"), 100),
                    "image": _bounded(raw.get("image"), 4000),
                    "score": raw.get("score") if isinstance(raw.get("score"), (int, float)) else None,
                    "highlights": [_bounded(item, 1600) for item in highlights[:8]],
                    "text": _bounded(raw.get("text"), 8000),
                }
            )
        return {"results": results, "diagnostics": self._diagnostics(data)}
