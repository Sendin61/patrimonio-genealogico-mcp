from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_LAB_DIRNAME = "ROB-Genealogy-Lab"
DEFAULT_PORT = 8877


@dataclass(frozen=True, slots=True)
class LabPaths:
    root: Path
    app: Path
    extension: Path
    data: Path
    cache: Path
    logs: Path
    config: Path

    def ensure(self) -> "LabPaths":
        for path in (
            self.root,
            self.app,
            self.extension,
            self.data,
            self.cache,
            self.logs,
            self.config,
        ):
            path.mkdir(parents=True, exist_ok=True)
        return self


def _downloads_dir() -> Path:
    override = os.getenv("ROB_LAB_DOWNLOADS_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / "Downloads").resolve()


def resolve_lab_paths(*, create: bool = False) -> LabPaths:
    root_override = os.getenv("ROB_LAB_HOME", "").strip()
    root = (
        Path(root_override).expanduser().resolve()
        if root_override
        else _downloads_dir() / DEFAULT_LAB_DIRNAME
    )
    paths = LabPaths(
        root=root,
        app=root / "app",
        extension=root / "extension",
        data=root / "data",
        cache=root / "cache",
        logs=root / "logs",
        config=root / "config",
    )
    return paths.ensure() if create else paths


def lab_port() -> int:
    raw = os.getenv("ROB_LAB_PORT", str(DEFAULT_PORT)).strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_PORT
    return value if 1 <= value <= 65535 else DEFAULT_PORT
