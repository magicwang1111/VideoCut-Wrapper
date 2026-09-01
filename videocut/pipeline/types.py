from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

PipelineJunctionType = Literal["flash-black", "dissolve", "cut", "zoom-dissolve"]
BgmSource = Literal["catalog", "template", "bgm-avatar"]
SubtitleLanguageMode = Literal["source", "translation", "bilingual", "auto"]
SubtitlePosition = Literal[
    "bottom-left", "bottom", "bottom-right", "middle", "top-left", "top", "top-right"
]


@dataclass(slots=True)
class PipelineClipConfig:
    src: str | None = None
    source_index: int | None = None
    trim_start: float = 0.0
    trim_end: float = 0.0
    trim_duration: float | None = None


@dataclass(slots=True)
class PipelineTransitionConfig:
    type: PipelineJunctionType
    duration: float
    scale: float | None = None


@dataclass(slots=True)
class PipelineOutputConfig:
    filename: str | None = None


@dataclass(slots=True)
class PipelineBgmConfig:
    enabled: bool = True
    source: BgmSource = "catalog"
    dir: str | None = None
    category: str | None = None
    filename: str | None = None
    volume: float = 0.3
    fade_out: float = 0.0


@dataclass(slots=True)
class PipelineSubtitleConfig:
    enabled: bool = True
    definition: int = 122
    target_language: str = "auto"
    language_mode: SubtitleLanguageMode = "source"
    accurate_mode: bool = True
    need_wordlist: bool = True
    adapt_words: str = ""
    font_name: str = "simkai.ttf"
    font_size: int = 40
    font_color: str = "#FFFFFF"
    font_alpha: float = 0.9
    position: SubtitlePosition = "bottom"
    auto_wrap: bool = True
    max_chars_per_line: int = 10
    margin_v: int = 200
    strip_punctuation: bool = True
    max_chars_per_cue: int = 10


@dataclass(slots=True)
class PipelineVariableDef:
    type: Literal["number", "boolean", "select"]
    required: bool = False
    default: Any = None
    min: float | None = None
    max: float | None = None
    options: list[str] | None = None


@dataclass(slots=True)
class PipelineConfig:
    mode: Literal["pipeline"]
    clips: list[PipelineClipConfig]
    name: str | None = None
    required_clip_count: int | None = None
    preset: str = "auto"
    quality: str = "high"
    output: PipelineOutputConfig | None = None
    transitions: list[PipelineTransitionConfig] | None = None
    default_transition: PipelineTransitionConfig | None = None
    bgm: PipelineBgmConfig | None = None
    preserve_original_audio: bool = True
    subtitle: PipelineSubtitleConfig | None = None
    variables: dict[str, PipelineVariableDef] | None = None
    overridable: list[str] | None = None


@dataclass(slots=True)
class ResolvedPipelineClip:
    key: str
    src: str
    source_index: int
    probed_duration: float
    trim_start: float
    trim_end: float
    trim_duration: float | None
    effective_duration: float
