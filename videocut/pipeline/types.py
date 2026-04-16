from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PipelineJunctionType = Literal["flash-black", "dissolve", "cut"]


@dataclass(slots=True)
class PipelineClipConfig:
    src: str
    trim_start: float = 0.0
    trim_end: float = 0.0


@dataclass(slots=True)
class PipelineTransitionConfig:
    type: PipelineJunctionType
    duration: float


@dataclass(slots=True)
class PipelineOutputConfig:
    filename: str | None = None


@dataclass(slots=True)
class PipelineBgmConfig:
    enabled: bool = True       # 默认启用（配置了 bgm 段即生效）
    dir: str | None = None     # None → 运行时解析为 <root>/input/bgm
    volume: float = 0.3        # 0.0–1.0
    fade_out: float = 0.0      # 结尾淡出秒数，默认 0 = 不淡出


@dataclass(slots=True)
class PipelineConfig:
    mode: Literal["pipeline"]
    clips: list[PipelineClipConfig]
    preset: str = "auto"
    quality: str = "high"
    output: PipelineOutputConfig | None = None
    transitions: list[PipelineTransitionConfig] | None = None
    default_transition: PipelineTransitionConfig | None = None
    bgm: PipelineBgmConfig | None = None


@dataclass(slots=True)
class ResolvedPipelineClip:
    key: str
    src: str
    probed_duration: float
    trim_start: float
    trim_end: float
    effective_duration: float

