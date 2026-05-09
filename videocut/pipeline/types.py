from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

PipelineJunctionType = Literal["flash-black", "dissolve", "cut", "zoom-dissolve"]


@dataclass(slots=True)
class PipelineClipConfig:
    src: str | None = None
    trim_start: float = 0.0
    trim_end: float = 0.0


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
    dir: str | None = None
    category: str | None = None
    filename: str | None = None
    volume: float = 0.3
    fade_out: float = 0.0


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
    preset: str = "auto"
    quality: str = "high"
    output: PipelineOutputConfig | None = None
    transitions: list[PipelineTransitionConfig] | None = None
    default_transition: PipelineTransitionConfig | None = None
    bgm: PipelineBgmConfig | None = None
    variables: dict[str, PipelineVariableDef] | None = None
    overridable: list[str] | None = None


@dataclass(slots=True)
class ResolvedPipelineClip:
    key: str
    src: str
    probed_duration: float
    trim_start: float
    trim_end: float
    effective_duration: float
