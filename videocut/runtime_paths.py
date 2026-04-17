from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

_CONTAINER_ROOTS = (PurePosixPath("/srv/videocut"), PurePosixPath("/app"))


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _is_windows() -> bool:
    return os.name == "nt"


def resolve_runtime_path(
    raw_value: str | os.PathLike[str] | None,
    default_path: str | os.PathLike[str],
    *,
    root_dir: str | os.PathLike[str] | None = None,
) -> Path:
    base_dir = Path(root_dir).resolve() if root_dir is not None else project_root()
    if raw_value is None or not str(raw_value).strip():
        return Path(default_path).resolve()

    raw = str(raw_value).strip()
    if _is_windows():
        normalized = raw.replace("\\", "/")
        posix_path = PurePosixPath(normalized)
        for container_root in _CONTAINER_ROOTS:
            try:
                relative = posix_path.relative_to(container_root)
            except ValueError:
                continue
            return (base_dir / Path(*relative.parts)).resolve()

    return Path(raw).resolve()
