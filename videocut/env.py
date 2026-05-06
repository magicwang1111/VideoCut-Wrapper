from __future__ import annotations

import os
import re
from pathlib import Path

_ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LOADED_ENV_FILES: set[Path] = set()


def load_project_env(root_dir: str | Path | None = None) -> None:
    """Load project .env values without overriding real environment variables."""
    base_dir = Path(root_dir).resolve() if root_dir is not None else Path(__file__).resolve().parents[1]
    env_path = base_dir / ".env"
    if not env_path.is_file():
        return
    resolved_path = env_path.resolve()
    if resolved_path in _LOADED_ENV_FILES:
        return
    _LOADED_ENV_FILES.add(resolved_path)

    try:
        from dotenv import load_dotenv
    except ImportError:
        _load_simple_env(resolved_path)
        return

    load_dotenv(resolved_path, override=False)


def _load_simple_env(env_path: Path) -> None:
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not _ENV_KEY_PATTERN.fullmatch(key) or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value
