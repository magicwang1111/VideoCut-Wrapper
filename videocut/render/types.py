from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from videocut.presets import ResolutionPreset
from videocut.registry import TemplateInfo


@dataclass(slots=True)
class ProbedVideoInfo(ResolutionPreset):
    duration: float


@dataclass(slots=True)
class RenderRequest:
    template_id: str
    template_info: TemplateInfo
    variables: dict[str, Any]
    preset: str
    quality: str
    output_filename: str | None
    project_dir: str
    task_id: str | None = None


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

