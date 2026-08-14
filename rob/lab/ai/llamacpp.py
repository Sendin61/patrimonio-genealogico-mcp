from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from ..config import resolve_lab_paths
from .base import AIProviderStatus


DEFAULT_AI_PORT = 8899
DEFAULT_CONTEXT_SIZE = 16384


def _ai_port() -> int:
    try:
        value = int(os.getenv("ROB_LAB_AI_PORT", str(DEFAULT_AI_PORT)))
    except ValueError:
        return DEFAULT_AI_PORT
    return value if 1 <= value <= 65535 else DEFAULT_AI_PORT


def _context_size() -> int:
    try:
        value = int(os.getenv("ROB_LAB_AI_CONTEXT", str(DEFAULT_CONTEXT_SIZE)))
    except ValueError:
        return DEFAULT_CONTEXT_SIZE
    return max(4096, min(value, 131072))


class LlamaCppProvider:
    """OpenAI-compatible client for a local llama-server instance."""

    def __init__(self, base_url: str | None = None, *, timeout: float = 120.0) -> None:
        self.base_url = (base_url or f"http://127.0.0.1:{_ai_port()}/v1").rstrip("/")
        self.timeout = timeout
        self._model_id: str | None = None

    async def status(self) -> AIProviderStatus:
        try:
            async with httpx.AsyncClient(timeout=2.5) as client:
                response = await client.get(f"{self.base_url}/models")
                response.raise_for_status()
                payload = response.json()
            models = payload.get("data") if isinstance(payload, dict) else None
            model = None
            if isinstance(models, list) and models and isinstance(models[0], dict):
                model = str(models[0].get("id") or "") or None
            self._model_id = model
            return AIProviderStatus(
                available=True,
                provider="llama.cpp",
                base_url=self.base_url,
                model=model,
            )
        except Exception as exc:
            return AIProviderStatus(
                available=False,
                provider="llama.cpp",
                base_url=self.base_url,
                detail=str(exc),
            )

    async def _model(self) -> str:
        if self._model_id:
            return self._model_id
        status = await self.status()
        return status.model or "local-model"

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": await self._model(),
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "rob_genealogy_response",
                    "strict": True,
                    "schema": json_schema,
                },
            }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/chat/completions", json=payload)
            response.raise_for_status()
            value = response.json()
        choices = value.get("choices") if isinstance(value, dict) else None
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("llama.cpp returned no chat choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise RuntimeError("llama.cpp returned no text content")
        return content.strip()


class LlamaCppRuntime:
    """Portable llama.cpp runtime kept under Downloads/ROB-Genealogy-Lab.

    The runtime deliberately does not download binaries or models silently. Installation
    will be handled by an explicit bootstrap action so the user always knows what is
    being added to the local project directory.
    """

    def __init__(self) -> None:
        paths = resolve_lab_paths(create=True)
        self.root = paths.root
        self.runtime_dir = self.root / "runtime" / "llama.cpp"
        self.models_dir = self.root / "models"
        self.logs_dir = paths.logs
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.provider = LlamaCppProvider()
        self._process: subprocess.Popen[bytes] | None = None

    def binary_path(self) -> Path | None:
        override = os.getenv("ROB_LAB_LLAMA_SERVER", "").strip()
        candidates: list[Path] = []
        if override:
            candidates.append(Path(override).expanduser())
        candidates.extend(
            [
                self.runtime_dir / "llama-server.exe",
                self.root / "runtime" / "llama-server.exe",
            ]
        )
        on_path = shutil.which("llama-server") or shutil.which("llama-server.exe")
        if on_path:
            candidates.append(Path(on_path))
        return next((path.resolve() for path in candidates if path.is_file()), None)

    def model_path(self) -> Path | None:
        override = os.getenv("ROB_LAB_MODEL", "").strip()
        if override:
            path = Path(override).expanduser()
            return path.resolve() if path.is_file() else None

        models = sorted(self.models_dir.rglob("*.gguf"))
        if not models:
            return None

        def score(path: Path) -> tuple[int, str]:
            name = path.name.casefold()
            points = 0
            if "qwen3" in name and "4b" in name:
                points += 100
            if "q4_k_m" in name:
                points += 40
            elif "q4" in name:
                points += 25
            if "deepseek" in name and "7b" in name:
                points += 20
            return points, name

        return max(models, key=score).resolve()

    async def status(self) -> dict[str, Any]:
        provider = await self.provider.status()
        binary = self.binary_path()
        model = self.model_path()
        return {
            "running": provider.available,
            "provider": provider.to_dict(),
            "binary": str(binary) if binary else None,
            "model_file": str(model) if model else None,
            "runtime_dir": str(self.runtime_dir),
            "models_dir": str(self.models_dir),
        }

    async def ensure_running(self, *, wait_seconds: float = 25.0) -> bool:
        if (await self.provider.status()).available:
            return True
        binary = self.binary_path()
        model = self.model_path()
        if not binary or not model:
            return False

        log_path = self.logs_dir / "llama-server.log"
        log_handle = log_path.open("ab", buffering=0)
        command = [
            str(binary),
            "-m",
            str(model),
            "--host",
            "127.0.0.1",
            "--port",
            str(_ai_port()),
            "--ctx-size",
            str(_context_size()),
            "--n-gpu-layers",
            "auto",
            "--jinja",
        ]
        creationflags = 0
        if os.name == "nt":
            creationflags = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        self._process = subprocess.Popen(
            command,
            cwd=str(self.runtime_dir),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )

        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                return False
            if (await self.provider.status()).available:
                return True
            await asyncio.sleep(0.4)
        return False
