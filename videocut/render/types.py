from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from videocut.presets import ResolutionPreset


@dataclass(slots=True)
class ProbedVideoInfo(ResolutionPreset):
    duration: float


@dataclass(slots=True)
class RenderResult:
    task_id: str
    status: Literal["completed", "failed"]
    output_path: str | None = None
    duration: float | None = None
    error: str | None = None


@dataclass(slots=True)
class VideoClip:
    key: str
    src: str
    duration: float
