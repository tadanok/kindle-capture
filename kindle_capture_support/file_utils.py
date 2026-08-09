"""Shared atomic-output and path-safety helpers."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def create_sibling_temporary_path(path: Path) -> Path:
    """Create an unused temporary path beside a destination for atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=f".tmp{path.suffix}",
        dir=path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    temporary_path.unlink()
    return temporary_path


def ensure_distinct_paths(named_paths: dict[str, Path | None]) -> None:
    """Reject path collisions before any output file can be overwritten."""
    seen: dict[Path, str] = {}
    for name, path in named_paths.items():
        if path is None:
            continue
        resolved = path.expanduser().resolve()
        previous = seen.get(resolved)
        if previous is not None:
            raise ValueError(
                f"{previous} と {name} に同じファイルが指定されています: "
                f"{resolved}"
            )
        seen[resolved] = name
