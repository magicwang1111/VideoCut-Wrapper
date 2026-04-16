from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from videocut.errors import ConfigNotFoundError, ConfigParseError, VideoCutError
from videocut.pipeline.types import (
    PipelineBgmConfig,
    PipelineClipConfig,
    PipelineConfig,
    PipelineJunctionType,
    PipelineOutputConfig,
    PipelineTransitionConfig,
)


def is_pipeline_config(raw: Any) -> bool:
    return isinstance(raw, dict) and raw.get("mode") == "pipeline"


def load_raw_yaml(config_path: str | Path) -> Any:
    abs_path = Path(config_path).resolve()
    if not abs_path.exists():
        raise ConfigNotFoundError(str(abs_path))
    raw = abs_path.read_text(encoding="utf-8")
    try:
        return json.loads(raw) if abs_path.suffix.lower() == ".json" else yaml.safe_load(raw)
    except (json.JSONDecodeError, yaml.YAMLError, ValueError) as exc:
        raise ConfigParseError(str(abs_path), str(exc)) from exc


def parse_junction_type(raw: object, location: str) -> PipelineJunctionType:
    if raw in {"flash-black", "dissolve", "cut"}:
        return raw  # type: ignore[return-value]
    if raw is None:
        return "cut"
    raise VideoCutError(f'{location}: invalid transition type "{raw}", expected flash-black|dissolve|cut')


def parse_transition_config(raw: object, location: str) -> PipelineTransitionConfig:
    if not isinstance(raw, dict):
        raise VideoCutError(f"{location}: transition must be an object")
    return PipelineTransitionConfig(
        type=parse_junction_type(raw.get("type"), f"{location}.type"),
        duration=float(raw.get("duration")) if isinstance(raw.get("duration"), (int, float)) else 0.5,
    )


def parse_pipeline_config(raw: Any, config_path: str | Path) -> PipelineConfig:
    _ = config_path
    if not isinstance(raw, dict):
        raise VideoCutError("Pipeline config must be an object.")
    clips_raw = raw.get("clips")
    if not isinstance(clips_raw, list) or not clips_raw:
        raise VideoCutError('Pipeline config requires a non-empty "clips" array.')

    clips: list[PipelineClipConfig] = []
    for index, item in enumerate(clips_raw):
        if not isinstance(item, dict):
            raise VideoCutError(f"clips[{index}] must be an object with a src field.")
        src = item.get("src")
        if not isinstance(src, str) or not src.strip():
            raise VideoCutError(f"clips[{index}].src must be a non-empty string.")
        clips.append(
            PipelineClipConfig(
                src=src,
                trim_start=float(item.get("trim_start")) if isinstance(item.get("trim_start"), (int, float)) else 0.0,
                trim_end=float(item.get("trim_end")) if isinstance(item.get("trim_end"), (int, float)) else 0.0,
            )
        )

    transitions = None
    if isinstance(raw.get("transitions"), list):
        transitions = [
            parse_transition_config(item, f"transitions[{index}]")
            for index, item in enumerate(raw["transitions"])
        ]

    default_transition = (
        parse_transition_config(raw["default_transition"], "default_transition")
        if raw.get("default_transition") is not None
        else None
    )

    output_raw = raw.get("output")
    output = PipelineOutputConfig(filename=output_raw.get("filename")) if isinstance(output_raw, dict) else None

    bgm = parse_bgm_config(raw.get("bgm"))

    return PipelineConfig(
        mode="pipeline",
        preset=raw.get("preset") if isinstance(raw.get("preset"), str) else "auto",
        quality=raw.get("quality") if isinstance(raw.get("quality"), str) else "high",
        output=output,
        clips=clips,
        transitions=transitions,
        default_transition=default_transition,
        bgm=bgm,
    )


def parse_bgm_config(raw: object) -> PipelineBgmConfig | None:
    if not isinstance(raw, dict):
        return None
    return PipelineBgmConfig(
        enabled=bool(raw.get("enabled", True)),
        dir=raw.get("dir") if isinstance(raw.get("dir"), str) else None,
        volume=float(raw["volume"]) if isinstance(raw.get("volume"), (int, float)) else 0.3,
        fade_out=float(raw["fade_out"]) if isinstance(raw.get("fade_out"), (int, float)) else 0.0,
    )


def resolve_video_path(src: str, project_dir: Path, config_path: Path, index: int) -> str:
    path_obj = Path(src)
    if path_obj.is_absolute():
        if not path_obj.exists():
            raise VideoCutError(f"clips[{index}].src does not exist: {src}\n  Config: {config_path}")
        return str(path_obj)
    candidate = (project_dir / src).resolve()
    if not candidate.exists():
        raise VideoCutError(f"clips[{index}].src does not exist: {candidate}\n  Config: {config_path}")
    return str(candidate)


def resolve_junctions(
    clip_count: int,
    transitions: list[PipelineTransitionConfig] | None,
    default_transition: PipelineTransitionConfig | None,
) -> list[PipelineTransitionConfig]:
    fallback = default_transition or PipelineTransitionConfig(type="cut", duration=0.0)
    return [transitions[index] if transitions and index < len(transitions) else fallback for index in range(clip_count - 1)]


@dataclass(slots=True)
class ParsedPipelineContext:
    config: PipelineConfig
    project_dir: Path
    config_path: Path
    resolved_srcs: list[str]
    junctions: list[PipelineTransitionConfig]


def resolve_pipeline_config(config_path: str | Path) -> ParsedPipelineContext:
    abs_config_path = Path(config_path).resolve()
    project_dir = abs_config_path.parent
    raw = load_raw_yaml(abs_config_path)
    config = parse_pipeline_config(raw, abs_config_path)
    resolved_srcs = [resolve_video_path(clip.src, project_dir, abs_config_path, index) for index, clip in enumerate(config.clips)]
    junctions = resolve_junctions(len(config.clips), config.transitions, config.default_transition)
    return ParsedPipelineContext(
        config=config,
        project_dir=project_dir,
        config_path=abs_config_path,
        resolved_srcs=resolved_srcs,
        junctions=junctions,
    )

