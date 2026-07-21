from __future__ import annotations

import json
import subprocess
from pathlib import Path

from videocut.ffmpeg_config import resolve_runtime_video_settings, resolve_video_settings
from videocut.presets import get_quality_preset


def _ass_filter_path(path: Path) -> str:
    return path.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")


def probe_media(ffprobe: str, path: str | Path) -> dict[str, bool]:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {(result.stderr or '')[-1000:]}")
    streams = json.loads(result.stdout or "{}").get("streams", [])
    return {"video": any(item.get("codec_type") == "video" for item in streams),
            "audio": any(item.get("codec_type") == "audio" for item in streams)}


def burn_ass(input_video: str | Path, ass_path: str | Path, output_video: str | Path,
             ffmpeg: str, ffprobe: str, quality: str = "high", timeout: int = 3600) -> tuple[Path, str]:
    source, subtitle, target = Path(input_video).resolve(), Path(ass_path).resolve(), Path(output_video).resolve()
    streams = probe_media(ffprobe, source)
    if not streams["video"]:
        raise RuntimeError("Input has no video stream.")
    if not streams["audio"]:
        raise RuntimeError("Input has no audio stream.")
    settings = resolve_runtime_video_settings(ffmpeg, resolve_video_settings())
    preset = get_quality_preset(quality)
    fonts_dir = Path(__file__).resolve().parents[2] / "fonts"
    ass_filter = f"ass=filename='{subtitle.name}'"
    if fonts_dir.is_dir():
        ass_filter += f":fontsdir='{_ass_filter_path(fonts_dir)}'"
    command = [
        ffmpeg, "-v", "error", "-y", *settings.input_args(), "-i", str(source),
        "-map", "0:v:0", "-map", "0:a?", "-vf", ass_filter,
        *settings.output_args(preset), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(target),
    ]
    result = subprocess.run(command, cwd=subtitle.parent, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg subtitle burn failed: {(result.stderr or result.stdout or '')[-2000:]}")
    if not target.is_file() or target.stat().st_size == 0 or not probe_media(ffprobe, target)["video"]:
        raise RuntimeError("Subtitle burn output validation failed.")
    return target, settings.encoder
