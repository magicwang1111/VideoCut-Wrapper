from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from videocut.errors import ConfigNotFoundError, ConfigParseError, VideoCutError
from videocut.pipeline.config import parse_bgm_config
from videocut.pipeline.types import PipelineBgmConfig


@dataclass(slots=True)
class ProjectOutputConfig:
    filename: str | None = None


@dataclass(slots=True)
class ProjectConfig:
    template: str
    preset: str | None
    quality: str | None
    output: ProjectOutputConfig | None
    variables: dict[str, Any]
    bgm: PipelineBgmConfig | None = None


def parse_project_config(config_path: str | Path) -> ProjectConfig:
    abs_path = Path(config_path).resolve()
    if not abs_path.exists():
        raise ConfigNotFoundError(str(abs_path))

    raw = abs_path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw) if abs_path.suffix.lower() == ".json" else yaml.safe_load(raw)
    except (json.JSONDecodeError, yaml.YAMLError, ValueError) as exc:
        raise ConfigParseError(str(abs_path), str(exc)) from exc

    if not isinstance(parsed, dict):
        raise VideoCutError(f"Config root must be an object, got {type(parsed).__name__}.")

    template = parsed.get("template")
    if not isinstance(template, str) or not template.strip():
        raise VideoCutError('Config must include a string "template" field.')

    output_raw = parsed.get("output")
    output = None
    if isinstance(output_raw, dict):
        output = ProjectOutputConfig(filename=output_raw.get("filename"))

    variables = parsed.get("variables")
    bgm = parse_bgm_config(parsed.get("bgm"))
    return ProjectConfig(
        template=template,
        preset=parsed.get("preset") if isinstance(parsed.get("preset"), str) else None,
        quality=parsed.get("quality") if isinstance(parsed.get("quality"), str) else None,
        output=output,
        variables=variables if isinstance(variables, dict) else {},
        bgm=bgm,
    )


def get_project_dir(config_path: str | Path) -> Path:
    return Path(config_path).resolve().parent

