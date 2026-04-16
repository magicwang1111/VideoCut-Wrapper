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
    duration_raw = raw.get("duration")
    duration = float(duration_raw) if isinstance(duration_raw, (int, float)) else 0.5
    return PipelineTransitionConfig(
        type=parse_junction_type(raw.get("type"), f"{location}.type"),
        duration=duration,
    )


def parse_output_config(raw: object) -> PipelineOutputConfig | None:
    if not isinstance(raw, dict):
        return None
    filename = raw.get("filename")
    return PipelineOutputConfig(filename=str(filename) if isinstance(filename, str) and filename.strip() else None)


def parse_bgm_config(raw: object) -> PipelineBgmConfig | None:
    if not isinstance(raw, dict):
        return None
    return PipelineBgmConfig(
        enabled=bool(raw.get("enabled", True)),
        dir=str(raw["dir"]) if isinstance(raw.get("dir"), str) and str(raw["dir"]).strip() else None,
        volume=float(raw["volume"]) if isinstance(raw.get("volume"), (int, float)) else 0.3,
        fade_out=float(raw["fade_out"]) if isinstance(raw.get("fade_out"), (int, float)) else 0.0,
    )


def _parse_clip_config(raw: object, location: str, require_src: bool) -> PipelineClipConfig:
    if not isinstance(raw, dict):
        raise VideoCutError(f"{location} must be an object.")
    src_raw = raw.get("src")
    src: str | None = None
    if src_raw is not None:
        if not isinstance(src_raw, str) or not src_raw.strip():
            raise VideoCutError(f"{location}.src must be a non-empty string.")
        src = src_raw.strip()
    elif require_src:
        raise VideoCutError(f"{location}.src must be a non-empty string.")
    return PipelineClipConfig(
        src=src,
        trim_start=float(raw.get("trim_start")) if isinstance(raw.get("trim_start"), (int, float)) else 0.0,
        trim_end=float(raw.get("trim_end")) if isinstance(raw.get("trim_end"), (int, float)) else 0.0,
    )


def parse_pipeline_config(
    raw: Any,
    config_path: str | Path,
    *,
    require_name: bool = False,
    require_clip_src: bool = False,
) -> PipelineConfig:
    _ = config_path
    if not isinstance(raw, dict):
        raise VideoCutError("Pipeline config must be an object.")

    name_raw = raw.get("name")
    name = name_raw.strip() if isinstance(name_raw, str) and name_raw.strip() else None
    if require_name and not name:
        raise VideoCutError('Pipeline config requires a non-empty "name" field.')

    clips_raw = raw.get("clips")
    if not isinstance(clips_raw, list) or not clips_raw:
        raise VideoCutError('Pipeline config requires a non-empty "clips" array.')
    clips = [_parse_clip_config(item, f"clips[{index}]", require_clip_src) for index, item in enumerate(clips_raw)]

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

    return PipelineConfig(
        mode="pipeline",
        name=name,
        preset=raw.get("preset") if isinstance(raw.get("preset"), str) else "auto",
        quality=raw.get("quality") if isinstance(raw.get("quality"), str) else "high",
        output=parse_output_config(raw.get("output")),
        clips=clips,
        transitions=transitions,
        default_transition=default_transition,
        bgm=parse_bgm_config(raw.get("bgm")),
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
    config = parse_pipeline_config(raw, abs_config_path, require_clip_src=True)
    resolved_srcs = [
        resolve_video_path(clip.src or "", project_dir, abs_config_path, index)
        for index, clip in enumerate(config.clips)
    ]
    junctions = resolve_junctions(len(config.clips), config.transitions, config.default_transition)
    return ParsedPipelineContext(
        config=config,
        project_dir=project_dir,
        config_path=abs_config_path,
        resolved_srcs=resolved_srcs,
        junctions=junctions,
    )


def _clone_transition(config: PipelineTransitionConfig | None) -> PipelineTransitionConfig | None:
    if config is None:
        return None
    return PipelineTransitionConfig(type=config.type, duration=config.duration)


def _clone_output(config: PipelineOutputConfig | None) -> PipelineOutputConfig | None:
    if config is None:
        return None
    return PipelineOutputConfig(filename=config.filename)


def _clone_bgm(config: PipelineBgmConfig | None) -> PipelineBgmConfig | None:
    if config is None:
        return None
    return PipelineBgmConfig(
        enabled=config.enabled,
        dir=config.dir,
        volume=config.volume,
        fade_out=config.fade_out,
    )


def _parse_override_index(raw: object, location: str) -> int:
    if not isinstance(raw, int) or raw < 0:
        raise VideoCutError(f"{location}.index must be a non-negative integer.")
    return raw


def _parse_clip_override_map(raw: object) -> dict[int, dict[str, float]]:
    if raw is None:
        return {}
    if not isinstance(raw, list):
        raise VideoCutError("overrides.clip_overrides must be an array.")
    overrides: dict[int, dict[str, float]] = {}
    for index, item in enumerate(raw):
        location = f"overrides.clip_overrides[{index}]"
        if not isinstance(item, dict):
            raise VideoCutError(f"{location} must be an object.")
        override_index = _parse_override_index(item.get("index"), location)
        values: dict[str, float] = {}
        if "trim_start" in item:
            if not isinstance(item.get("trim_start"), (int, float)):
                raise VideoCutError(f"{location}.trim_start must be a number.")
            values["trim_start"] = float(item["trim_start"])
        if "trim_end" in item:
            if not isinstance(item.get("trim_end"), (int, float)):
                raise VideoCutError(f"{location}.trim_end must be a number.")
            values["trim_end"] = float(item["trim_end"])
        overrides[override_index] = values
    return overrides


def _parse_transition_override_map(raw: object) -> dict[int, dict[str, object]]:
    if raw is None:
        return {}
    if not isinstance(raw, list):
        raise VideoCutError("overrides.transition_overrides must be an array.")
    overrides: dict[int, dict[str, object]] = {}
    for index, item in enumerate(raw):
        location = f"overrides.transition_overrides[{index}]"
        if not isinstance(item, dict):
            raise VideoCutError(f"{location} must be an object.")
        override_index = _parse_override_index(item.get("index"), location)
        values: dict[str, object] = {}
        if "type" in item:
            values["type"] = parse_junction_type(item.get("type"), f"{location}.type")
        if "duration" in item:
            if not isinstance(item.get("duration"), (int, float)):
                raise VideoCutError(f"{location}.duration must be a number.")
            values["duration"] = float(item["duration"])
        overrides[override_index] = values
    return overrides


def bind_pipeline_config(
    config: PipelineConfig,
    clip_count: int,
    overrides: dict[str, Any] | None = None,
) -> PipelineConfig:
    if clip_count <= 0:
        raise VideoCutError("Pipeline render requires at least one clip.")

    overrides = overrides or {}
    clip_override_map = _parse_clip_override_map(overrides.get("clip_overrides"))
    transition_override_map = _parse_transition_override_map(overrides.get("transition_overrides"))
    transition_count = max(clip_count - 1, 0)

    invalid_clip_indexes = sorted(index for index in clip_override_map if index >= clip_count)
    if invalid_clip_indexes:
        raise VideoCutError(f"clip_overrides index out of range: {invalid_clip_indexes}")
    invalid_transition_indexes = sorted(index for index in transition_override_map if index >= transition_count)
    if invalid_transition_indexes:
        raise VideoCutError(f"transition_overrides index out of range: {invalid_transition_indexes}")

    default_transition = (
        parse_transition_config(overrides["default_transition"], "overrides.default_transition")
        if overrides.get("default_transition") is not None
        else _clone_transition(config.default_transition)
    )
    if default_transition is None:
        default_transition = PipelineTransitionConfig(type="cut", duration=0.0)

    output = _clone_output(config.output)
    if overrides.get("output") is not None:
        output = parse_output_config(overrides.get("output"))

    bgm = _clone_bgm(config.bgm)
    if overrides.get("bgm") is not None:
        bgm = parse_bgm_config(overrides.get("bgm"))

    bound_clips: list[PipelineClipConfig] = []
    for index in range(clip_count):
        source = config.clips[index] if index < len(config.clips) else PipelineClipConfig()
        clip = PipelineClipConfig(trim_start=source.trim_start, trim_end=source.trim_end)
        for key, value in clip_override_map.get(index, {}).items():
            setattr(clip, key, value)
        bound_clips.append(clip)

    bound_transitions: list[PipelineTransitionConfig] = []
    for index in range(transition_count):
        source = (
            _clone_transition(config.transitions[index])
            if config.transitions and index < len(config.transitions)
            else _clone_transition(default_transition)
        )
        transition = source or PipelineTransitionConfig(type="cut", duration=0.0)
        override_values = transition_override_map.get(index, {})
        if "type" in override_values:
            transition.type = override_values["type"]  # type: ignore[assignment]
        if "duration" in override_values:
            transition.duration = float(override_values["duration"])
        bound_transitions.append(transition)

    preset = overrides.get("preset") if isinstance(overrides.get("preset"), str) else config.preset
    quality = overrides.get("quality") if isinstance(overrides.get("quality"), str) else config.quality

    return PipelineConfig(
        mode="pipeline",
        name=config.name,
        preset=preset,
        quality=quality,
        output=output,
        clips=bound_clips,
        transitions=bound_transitions or None,
        default_transition=default_transition,
        bgm=bgm,
    )


def build_pipeline_context(
    config: PipelineConfig,
    resolved_srcs: list[str],
    config_path: str | Path,
    overrides: dict[str, Any] | None = None,
) -> ParsedPipelineContext:
    abs_config_path = Path(config_path).resolve()
    bound = bind_pipeline_config(config, len(resolved_srcs), overrides)
    return ParsedPipelineContext(
        config=bound,
        project_dir=abs_config_path.parent,
        config_path=abs_config_path,
        resolved_srcs=list(resolved_srcs),
        junctions=resolve_junctions(len(bound.clips), bound.transitions, bound.default_transition),
    )
