from __future__ import annotations

import json
import re
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
    PipelineSubtitleConfig,
    PipelineTransitionConfig,
    PipelineVariableDef,
)

_SUBTITLE_LANGUAGE_MODES = {"source", "translation", "bilingual", "auto"}
_SUBTITLE_POSITIONS = {"bottom-left", "bottom", "bottom-right", "middle", "top-left", "top", "top-right"}
_SUBTITLE_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def parse_subtitle_config(raw: Any) -> PipelineSubtitleConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise VideoCutError("subtitle must be an object.")
    enabled = raw.get("enabled", True)
    definition = raw.get("definition", 122)
    language_mode = raw.get("language_mode", "source")
    target_language = raw.get("target_language", "auto")
    position = raw.get("position", "bottom")
    font_name = raw.get("font_name", "simkai.ttf")
    font_size = raw.get("font_size", 40)
    font_color = raw.get("font_color", "#FFFFFF")
    font_alpha = raw.get("font_alpha", 0.9)
    max_chars = raw.get("max_chars_per_line", 10)
    margin_v = raw.get("margin_v", 200)
    strip_punctuation = raw.get("strip_punctuation", True)
    max_chars_per_cue = raw.get("max_chars_per_cue", 10)
    for field, value in (("enabled", enabled), ("accurate_mode", raw.get("accurate_mode", True)),
                         ("need_wordlist", raw.get("need_wordlist", True)), ("auto_wrap", raw.get("auto_wrap", True)),
                         ("strip_punctuation", strip_punctuation)):
        if not isinstance(value, bool):
            raise VideoCutError(f"subtitle.{field} must be a boolean.")
    if not isinstance(definition, int) or isinstance(definition, bool) or definition <= 0:
        raise VideoCutError("subtitle.definition must be a positive integer.")
    if language_mode not in _SUBTITLE_LANGUAGE_MODES:
        raise VideoCutError("subtitle.language_mode is invalid.")
    if not isinstance(target_language, str) or not target_language.strip():
        raise VideoCutError("subtitle.target_language must be a non-empty string.")
    if position not in _SUBTITLE_POSITIONS:
        raise VideoCutError("subtitle.position is invalid.")
    if not isinstance(font_name, str) or Path(font_name).name != font_name or font_name not in {"simkai.ttf", "msyh.ttc", "simhei.ttf", "SIMHEI.TTF"}:
        raise VideoCutError("subtitle.font_name must be an allowed bundled/system subtitle font.")
    if not isinstance(font_size, int) or isinstance(font_size, bool) or not 8 <= font_size <= 200:
        raise VideoCutError("subtitle.font_size must be an integer between 8 and 200.")
    if not isinstance(font_color, str) or not _SUBTITLE_COLOR_RE.fullmatch(font_color):
        raise VideoCutError("subtitle.font_color must use #RRGGBB.")
    if not isinstance(font_alpha, (int, float)) or isinstance(font_alpha, bool) or not 0 < float(font_alpha) <= 1:
        raise VideoCutError("subtitle.font_alpha must be in (0, 1].")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or not 4 <= max_chars <= 100:
        raise VideoCutError("subtitle.max_chars_per_line must be an integer between 4 and 100.")
    if not isinstance(margin_v, int) or isinstance(margin_v, bool) or not 0 <= margin_v <= 1080:
        raise VideoCutError("subtitle.margin_v must be an integer between 0 and 1080.")
    if not isinstance(max_chars_per_cue, int) or isinstance(max_chars_per_cue, bool) or not 1 <= max_chars_per_cue <= 80:
        raise VideoCutError("subtitle.max_chars_per_cue must be an integer between 1 and 80.")
    adapt_words = raw.get("adapt_words", "")
    if not isinstance(adapt_words, str):
        raise VideoCutError("subtitle.adapt_words must be a string.")
    return PipelineSubtitleConfig(
        enabled=enabled,
        definition=definition,
        target_language=target_language.strip(),
        language_mode=language_mode,
        accurate_mode=raw.get("accurate_mode", True),
        need_wordlist=raw.get("need_wordlist", True),
        adapt_words=adapt_words,
        font_name=font_name,
        font_size=font_size,
        font_color=font_color.upper(),
        font_alpha=float(font_alpha),
        position=position,
        auto_wrap=raw.get("auto_wrap", True),
        max_chars_per_line=max_chars,
        margin_v=margin_v,
        strip_punctuation=strip_punctuation,
        max_chars_per_cue=max_chars_per_cue,
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
    if raw in {"flash-black", "dissolve", "cut", "zoom-dissolve"}:
        return raw  # type: ignore[return-value]
    if raw is None:
        return "cut"
    raise VideoCutError(f'{location}: invalid transition type "{raw}", expected flash-black|dissolve|cut|zoom-dissolve')


def parse_transition_config(raw: object, location: str) -> PipelineTransitionConfig:
    if not isinstance(raw, dict):
        raise VideoCutError(f"{location}: transition must be an object")
    duration_raw = raw.get("duration")
    duration = float(duration_raw) if isinstance(duration_raw, (int, float)) else 0.5
    scale_raw = raw.get("scale")
    scale = float(scale_raw) if isinstance(scale_raw, (int, float)) else None
    return PipelineTransitionConfig(
        type=parse_junction_type(raw.get("type"), f"{location}.type"),
        duration=duration,
        scale=scale,
    )


def parse_output_config(raw: object) -> PipelineOutputConfig | None:
    if not isinstance(raw, dict):
        return None
    filename = raw.get("filename")
    return PipelineOutputConfig(filename=str(filename) if isinstance(filename, str) and filename.strip() else None)


def _optional_str(value: object) -> str | None:
    return str(value).strip() if isinstance(value, str) and str(value).strip() else None


def _parse_bgm_source(raw: object, base: PipelineBgmConfig | None = None) -> str:
    if not isinstance(raw, dict) or "source" not in raw:
        return base.source if base else "catalog"
    source = _optional_str(raw.get("source"))
    if source not in {"catalog", "template"}:
        raise VideoCutError('bgm.source must be "catalog" or "template".')
    return source


def parse_bgm_config(raw: object, base: PipelineBgmConfig | None = None) -> PipelineBgmConfig | None:
    if not isinstance(raw, dict):
        return base
    if "file" in raw:
        raise VideoCutError("bgm.file is not supported; use bgm.category and optional bgm.filename.")
    category = _optional_str(raw.get("category")) if "category" in raw else (base.category if base else None)
    filename = _optional_str(raw.get("filename")) if "filename" in raw else (base.filename if base else None)
    if "category" in raw and "filename" not in raw:
        filename = None
    return PipelineBgmConfig(
        enabled=bool(raw["enabled"]) if "enabled" in raw else (base.enabled if base else True),
        source=_parse_bgm_source(raw, base),  # type: ignore[arg-type]
        dir=_optional_str(raw.get("dir")) if "dir" in raw else (base.dir if base else None),
        category=category,
        filename=filename,
        volume=float(raw["volume"]) if isinstance(raw.get("volume"), (int, float)) else (base.volume if base else 0.3),
        fade_out=float(raw["fade_out"]) if isinstance(raw.get("fade_out"), (int, float)) else (base.fade_out if base else 0.0),
    )


def parse_variable_def(raw: object, name: str) -> PipelineVariableDef:
    if not isinstance(raw, dict):
        raise VideoCutError(f'variables.{name}: must be an object')
    var_type = raw.get("type")
    if var_type not in {"number", "boolean", "select"}:
        raise VideoCutError(f'variables.{name}: invalid type "{var_type}", expected number|boolean|select')
    required = bool(raw.get("required", False))
    default = raw.get("default")
    min_raw = raw.get("min")
    min_val = float(min_raw) if isinstance(min_raw, (int, float)) else None
    max_raw = raw.get("max")
    max_val = float(max_raw) if isinstance(max_raw, (int, float)) else None
    options_raw = raw.get("options")
    options: list[str] | None = None
    if options_raw is not None:
        if isinstance(options_raw, list) and all(isinstance(o, str) for o in options_raw):
            options = list(options_raw)
        else:
            raise VideoCutError(f'variables.{name}: options must be a list of strings')
    if var_type == "select" and not options:
        raise VideoCutError(f'variables.{name}: select type requires a non-empty options list')
    var_def = PipelineVariableDef(
        type=var_type,  # type: ignore[arg-type]
        required=required,
        default=default,
        min=min_val,
        max=max_val,
        options=options,
    )
    if default is not None:
        try:
            validate_variables({name: var_def}, {name: default})
        except VideoCutError as exc:
            raise VideoCutError(f'variables.{name}: invalid default — {exc}') from exc
    return var_def


def parse_variables(raw: object) -> dict[str, PipelineVariableDef] | None:
    if not isinstance(raw, dict) or not raw:
        return None
    return {key: parse_variable_def(value, key) for key, value in raw.items()}


def validate_variables(schema: dict[str, PipelineVariableDef], values: dict[str, object]) -> None:
    for name, var_def in schema.items():
        value = values.get(name)
        has_value = name in values
        if var_def.required and not has_value and var_def.default is None:
            raise VideoCutError(f'variable "{name}" is required but not provided')
        if not has_value:
            continue
        if var_def.type == "number":
            if not isinstance(value, (int, float)):
                raise VideoCutError(f'variable "{name}" must be a number')
            num_val = float(value)  # type: ignore[arg-type]
            if var_def.min is not None and num_val < var_def.min:
                raise VideoCutError(f'variable "{name}" value {num_val} is less than min {var_def.min}')
            if var_def.max is not None and num_val > var_def.max:
                raise VideoCutError(f'variable "{name}" value {num_val} is greater than max {var_def.max}')
        elif var_def.type == "select":
            if var_def.options and value not in var_def.options:
                raise VideoCutError(f'variable "{name}" value "{value}" is not one of {var_def.options}')
        elif var_def.type == "boolean":
            if not isinstance(value, bool) and value not in (0, 1):
                raise VideoCutError(f'variable "{name}" must be a boolean or 0/1')


def resolve_variable_values(
    schema: dict[str, PipelineVariableDef],
    raw_values: dict[str, object],
) -> dict[str, object]:
    merged: dict[str, object] = {}
    for name, var_def in schema.items():
        if var_def.default is not None:
            merged[name] = var_def.default
    merged.update(raw_values)
    validate_variables(schema, merged)
    # Normalise boolean 0/1 integers to bool after validation
    for name, var_def in schema.items():
        if var_def.type == "boolean" and name in merged and not isinstance(merged[name], bool):
            merged[name] = bool(merged[name])
    return merged


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
    source_index_raw = raw.get("source_index")
    source_index: int | None = None
    if source_index_raw is not None:
        if not isinstance(source_index_raw, int) or source_index_raw < 0:
            raise VideoCutError(f"{location}.source_index must be a non-negative integer.")
        source_index = source_index_raw
    trim_duration_raw = raw.get("trim_duration")
    trim_duration: float | None = None
    if trim_duration_raw is not None:
        if not isinstance(trim_duration_raw, (int, float)) or float(trim_duration_raw) <= 0:
            raise VideoCutError(f"{location}.trim_duration must be a positive number.")
        trim_duration = float(trim_duration_raw)
    return PipelineClipConfig(
        src=src,
        source_index=source_index,
        trim_start=float(raw.get("trim_start")) if isinstance(raw.get("trim_start"), (int, float)) else 0.0,
        trim_end=float(raw.get("trim_end")) if isinstance(raw.get("trim_end"), (int, float)) else 0.0,
        trim_duration=trim_duration,
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

    required_clip_count_raw = raw.get("required_clip_count")
    required_clip_count: int | None = None
    if required_clip_count_raw is not None:
        if not isinstance(required_clip_count_raw, int) or required_clip_count_raw <= 0:
            raise VideoCutError("required_clip_count must be a positive integer.")
        required_clip_count = required_clip_count_raw

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

    variables = parse_variables(raw.get("variables"))
    overridable_raw = raw.get("overridable")
    overridable = [str(item) for item in overridable_raw if isinstance(item, str)] if isinstance(overridable_raw, list) else None

    return PipelineConfig(
        mode="pipeline",
        name=name,
        required_clip_count=required_clip_count,
        preset=raw.get("preset") if isinstance(raw.get("preset"), str) else "auto",
        quality=raw.get("quality") if isinstance(raw.get("quality"), str) else "high",
        output=parse_output_config(raw.get("output")),
        clips=clips,
        transitions=transitions,
        default_transition=default_transition,
        bgm=parse_bgm_config(raw.get("bgm")),
        subtitle=parse_subtitle_config(raw.get("subtitle")),
        variables=variables,
        overridable=overridable,
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
    user_bgm_path: str | None = None


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
    return PipelineTransitionConfig(type=config.type, duration=config.duration, scale=config.scale)


def _clone_output(config: PipelineOutputConfig | None) -> PipelineOutputConfig | None:
    if config is None:
        return None
    return PipelineOutputConfig(filename=config.filename)


def _clone_bgm(config: PipelineBgmConfig | None) -> PipelineBgmConfig | None:
    if config is None:
        return None
    return PipelineBgmConfig(
        enabled=config.enabled,
        source=config.source,
        dir=config.dir,
        category=config.category,
        filename=config.filename,
        volume=config.volume,
        fade_out=config.fade_out,
    )


def _clone_subtitle(config: PipelineSubtitleConfig | None) -> PipelineSubtitleConfig | None:
    if config is None:
        return None
    return PipelineSubtitleConfig(**{field: getattr(config, field) for field in config.__dataclass_fields__})


def _parse_override_index(raw: object, location: str) -> int:
    if not isinstance(raw, int) or raw < 0:
        raise VideoCutError(f"{location}.index must be a non-negative integer.")
    return raw


def _parse_clip_override_map(raw: object) -> dict[int, dict[str, float | None]]:
    if raw is None:
        return {}
    if not isinstance(raw, list):
        raise VideoCutError("overrides.clip_overrides must be an array.")
    overrides: dict[int, dict[str, float | None]] = {}
    for index, item in enumerate(raw):
        location = f"overrides.clip_overrides[{index}]"
        if not isinstance(item, dict):
            raise VideoCutError(f"{location} must be an object.")
        override_index = _parse_override_index(item.get("index"), location)
        values: dict[str, float | None] = {}
        if "trim_start" in item:
            if not isinstance(item.get("trim_start"), (int, float)):
                raise VideoCutError(f"{location}.trim_start must be a number.")
            values["trim_start"] = float(item["trim_start"])
        if "trim_end" in item:
            if not isinstance(item.get("trim_end"), (int, float)):
                raise VideoCutError(f"{location}.trim_end must be a number.")
            values["trim_end"] = float(item["trim_end"])
        if "trim_duration" in item:
            duration_raw = item.get("trim_duration")
            if duration_raw is not None and (not isinstance(duration_raw, (int, float)) or float(duration_raw) <= 0):
                raise VideoCutError(f"{location}.trim_duration must be a positive number or null.")
            values["trim_duration"] = float(duration_raw) if duration_raw is not None else None
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
        if "scale" in item:
            if not isinstance(item.get("scale"), (int, float)):
                raise VideoCutError(f"{location}.scale must be a number.")
            values["scale"] = float(item["scale"])
        overrides[override_index] = values
    return overrides


def bind_pipeline_config(
    config: PipelineConfig,
    clip_count: int,
    overrides: dict[str, Any] | None = None,
) -> PipelineConfig:
    if clip_count <= 0:
        raise VideoCutError("Pipeline render requires at least one clip.")
    if config.required_clip_count is not None and clip_count != config.required_clip_count:
        raise VideoCutError(
            f"Pipeline requires exactly {config.required_clip_count} input clips, got {clip_count}."
        )

    overrides = overrides or {}
    clip_override_map = _parse_clip_override_map(overrides.get("clip_overrides"))
    transition_override_map = _parse_transition_override_map(overrides.get("transition_overrides"))
    uses_source_index = any(clip.source_index is not None for clip in config.clips)
    bound_clip_count = len(config.clips) if uses_source_index else clip_count
    transition_count = max(bound_clip_count - 1, 0)

    invalid_clip_indexes = sorted(index for index in clip_override_map if index >= bound_clip_count)
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
        bgm = parse_bgm_config(overrides.get("bgm"), base=bgm)
    subtitle = _clone_subtitle(config.subtitle)

    bound_clips: list[PipelineClipConfig] = []
    for index in range(bound_clip_count):
        source = config.clips[index] if index < len(config.clips) else PipelineClipConfig()
        clip = PipelineClipConfig(
            source_index=source.source_index if source.source_index is not None else index,
            trim_start=source.trim_start,
            trim_end=source.trim_end,
            trim_duration=source.trim_duration,
        )
        if clip.source_index >= clip_count:
            raise VideoCutError(
                f"clips[{index}].source_index {clip.source_index} is out of range for {clip_count} input clips."
            )
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
        if "scale" in override_values and isinstance(override_values["scale"], (int, float)):
            transition.scale = float(override_values["scale"])
        bound_transitions.append(transition)

    preset = overrides.get("preset") if isinstance(overrides.get("preset"), str) else config.preset
    quality = overrides.get("quality") if isinstance(overrides.get("quality"), str) else config.quality

    return PipelineConfig(
        mode="pipeline",
        name=config.name,
        required_clip_count=config.required_clip_count,
        preset=preset,
        quality=quality,
        output=output,
        clips=bound_clips,
        transitions=bound_transitions or None,
        default_transition=default_transition,
        bgm=bgm,
        subtitle=subtitle,
        variables=config.variables,
        overridable=config.overridable,
    )


def build_pipeline_context(
    config: PipelineConfig,
    resolved_srcs: list[str],
    config_path: str | Path,
    overrides: dict[str, Any] | None = None,
    *,
    user_bgm_path: str | None = None,
) -> ParsedPipelineContext:
    abs_config_path = Path(config_path).resolve()
    bound = bind_pipeline_config(config, len(resolved_srcs), overrides)
    bound_srcs = [
        resolved_srcs[clip.source_index if clip.source_index is not None else index]
        for index, clip in enumerate(bound.clips)
    ]
    return ParsedPipelineContext(
        config=bound,
        project_dir=abs_config_path.parent,
        config_path=abs_config_path,
        resolved_srcs=bound_srcs,
        junctions=resolve_junctions(len(bound.clips), bound.transitions, bound.default_transition),
        user_bgm_path=user_bgm_path,
    )
