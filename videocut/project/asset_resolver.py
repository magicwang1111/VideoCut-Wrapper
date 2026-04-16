from __future__ import annotations

from pathlib import Path
from typing import Any

from videocut.errors import AssetNotFoundError
from videocut.registry import TemplateInfo, VariableDefinition

FILE_TYPES = {"video", "video_list", "image", "audio"}


def resolve_assets(
    variables: dict[str, Any],
    schema: dict[str, VariableDefinition],
    project_dir: Path,
    template_info: TemplateInfo,
    root_dir: Path,
    config_path: str | None = None,
) -> dict[str, Any]:
    resolved = dict(variables)
    for key, value in variables.items():
        definition = schema.get(key)
        if definition is None or definition.type not in FILE_TYPES:
            continue

        if definition.type == "video_list":
            if not isinstance(value, list):
                continue
            resolved_list: list[str] = []
            for index, item in enumerate(value):
                if not isinstance(item, str) or not item:
                    continue
                abs_path = resolve_file_path(item, [project_dir, template_info.directory, root_dir])
                if abs_path is None:
                    raise AssetNotFoundError(item, f"{key}[{index}]", config_path)
                resolved_list.append(str(abs_path))
            resolved[key] = resolved_list
            continue

        if not isinstance(value, str) or not value:
            continue

        abs_path = resolve_file_path(value, [project_dir, template_info.directory, root_dir])
        if abs_path is None:
            raise AssetNotFoundError(value, key, config_path)
        resolved[key] = str(abs_path)

    return resolved


def resolve_file_path(file_path: str, search_dirs: list[Path]) -> Path | None:
    path_obj = Path(file_path)
    if path_obj.is_absolute():
        return path_obj if path_obj.exists() else None
    for directory in search_dirs:
        candidate = (directory / file_path).resolve()
        if candidate.exists():
            return candidate
    return None

