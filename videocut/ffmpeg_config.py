from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from functools import lru_cache

from videocut.presets import QualityPreset

_NVENC_PRESET_MAP = {
    "fast": "p4",
    "medium": "p5",
    "slow": "p6",
}


@dataclass(slots=True, frozen=True)
class FFmpegVideoSettings:
    encoder: str
    hwaccel: str | None = None

    @property
    def is_gpu_encoder(self) -> bool:
        return any(token in self.encoder for token in ("_nvenc", "_qsv", "_amf", "videotoolbox"))

    def input_args(self) -> list[str]:
        if not self.hwaccel:
            return []
        return ["-hwaccel", self.hwaccel]

    def output_args(self, qual_preset: QualityPreset, *, pix_fmt: str = "yuv420p") -> list[str]:
        args = ["-c:v", self.encoder]
        if self.encoder in {"libx264", "libx265"}:
            args.extend(["-preset", qual_preset.ffmpeg_preset, "-crf", str(qual_preset.crf)])
        elif self.encoder.endswith("_nvenc"):
            args.extend(
                [
                    "-preset",
                    _NVENC_PRESET_MAP.get(qual_preset.ffmpeg_preset, "p5"),
                    "-cq",
                    str(qual_preset.crf),
                ]
            )
        else:
            args.extend(["-preset", qual_preset.ffmpeg_preset, "-crf", str(qual_preset.crf)])
        if pix_fmt:
            args.extend(["-pix_fmt", pix_fmt])
        return args


def resolve_video_settings() -> FFmpegVideoSettings:
    encoder = (os.getenv("FFMPEG_ENCODER") or "auto").strip() or "auto"
    hwaccel = (os.getenv("FFMPEG_HWACCEL") or "").strip() or None
    return FFmpegVideoSettings(encoder=encoder, hwaccel=hwaccel)


def _probe_encoder(ffmpeg_path: str, encoder: str) -> bool:
    try:
        subprocess.run(
            [
                ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=640x360:r=30:d=0.2",
                "-frames:v",
                "1",
                "-c:v",
                encoder,
                "-f",
                "null",
                "-",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return True
    except Exception:
        return False


@lru_cache(maxsize=8)
def resolve_runtime_video_settings(ffmpeg_path: str, configured: FFmpegVideoSettings) -> FFmpegVideoSettings:
    if configured.encoder != "auto":
        return configured

    for encoder in ("h264_nvenc", "h264_qsv", "h264_amf"):
        if _probe_encoder(ffmpeg_path, encoder):
            return FFmpegVideoSettings(encoder=encoder, hwaccel=configured.hwaccel)
    return FFmpegVideoSettings(encoder="libx264", hwaccel=configured.hwaccel)
