from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from videocut.registry import VariableDefinition


@dataclass(slots=True)
class ValidationResult:
    valid: bool
    errors: list[str]
    merged: dict[str, Any]


FILE_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "video": (".mp4", ".mov", ".avi", ".mkv", ".webm"),
    "image": (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"),
    "audio": (".mp3", ".wav", ".aac", ".ogg", ".flac", ".m4a"),
}

COLOR_REGEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def validate_variables(
    schema: dict[str, VariableDefinition],
    user_vars: dict[str, Any],
) -> ValidationResult:
    errors: list[str] = []
    merged: dict[str, Any] = {}

    for key, definition in schema.items():
        user_value = user_vars.get(key)
        has_user_value = user_value is not None

        if definition.required and not has_user_value and definition.default is None:
            errors.append(f'{definition.label} ({key}) is required.')
            continue

        value = user_value if has_user_value else definition.default
        if value is None:
            continue

        type_error = _validate_type(key, definition, value)
        if type_error:
            errors.append(type_error)
            continue

        merged[key] = value

    for key in user_vars.keys():
        if key not in schema:
            errors.append(f'Unknown variable "{key}".')

    return ValidationResult(valid=not errors, errors=errors, merged=merged)


def _validate_type(key: str, definition: VariableDefinition, value: Any) -> str | None:
    label = definition.label
    var_type = definition.type

    if var_type == "text":
        if not isinstance(value, str):
            return f"{label} ({key}) must be a string."
        if definition.required and not value.strip():
            return f"{label} ({key}) cannot be empty."
        return None

    if var_type == "video_list":
        if not isinstance(value, list):
            return f"{label} ({key}) must be a list of video paths."
        if not value:
            return f"{label} ({key}) cannot be empty."
        allowed = FILE_EXTENSIONS["video"]
        for index, item in enumerate(value):
            if not isinstance(item, str):
                return f"{label} ({key})[{index}] must be a string path."
            if Path(item).suffix.lower() not in allowed:
                return (
                    f'{label} ({key})[{index}] uses unsupported format "{Path(item).suffix.lower()}". '
                    f"Allowed: {', '.join(allowed)}"
                )
        return None

    if var_type in {"video", "image", "audio"}:
        if not isinstance(value, str):
            return f"{label} ({key}) must be a string path."
        allowed = FILE_EXTENSIONS[var_type]
        ext = Path(value).suffix.lower()
        if ext not in allowed:
            return f'{label} ({key}) uses unsupported format "{ext}". Allowed: {", ".join(allowed)}'
        return None

    if var_type == "color":
        if not isinstance(value, str):
            return f"{label} ({key}) must be a string color value."
        if not COLOR_REGEX.match(value):
            return f'{label} ({key}) must use #RRGGBB format, got "{value}".'
        return None

    if var_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return f"{label} ({key}) must be numeric."
        if definition.min is not None and value < definition.min:
            return f"{label} ({key}) must be >= {definition.min}."
        if definition.max is not None and value > definition.max:
            return f"{label} ({key}) must be <= {definition.max}."
        return None

    if var_type == "boolean":
        if not isinstance(value, bool):
            return f"{label} ({key}) must be boolean."
        return None

    if var_type == "select":
        if not isinstance(value, str):
            return f"{label} ({key}) must be a string."
        if definition.options and value not in definition.options:
            return f'{label} ({key}) must be one of: {", ".join(definition.options)}.'
        return None

    return f'{label} ({key}) uses unsupported variable type "{var_type}".'

