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


def probe_duration(ffprobe: str, path: str | Path) -> float:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe duration failed: {(result.stderr or '')[-1000:]}")
    duration = float(json.loads(result.stdout or "{}").get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise RuntimeError("Input video duration is invalid.")
    return duration


def burn_ass(input_video: str | Path, ass_path: str | Path, output_video: str | Path,
             ffmpeg: str, ffprobe: str, quality: str = "high", timeout: int = 3600,
             bgm_path: str | Path | None = None, bgm_volume: float = 1.0,
             bgm_fade_out: float = 0.0, preserve_original_audio: bool = True) -> tuple[Path, str]:
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
    command = [ffmpeg, "-v", "error", "-y", *settings.input_args(), "-i", str(source)]
    if bgm_path is not None:
        bgm = Path(bgm_path).resolve()
        if not bgm.is_file():
            raise RuntimeError(f"BGM file does not exist: {bgm}")
        duration = probe_duration(ffprobe, source)
        bgm_filter = (
            f"[1:a:0]volume={bgm_volume:.4f},"
            f"atrim=end={duration:.6f}"
        )
        if bgm_fade_out > 0:
            fade_start = max(0.0, duration - bgm_fade_out)
            bgm_filter += f",afade=t=out:st={fade_start:.6f}:d={bgm_fade_out:.4f}"
        bgm_filter += "[bgm]"
        output_audio_label = "[bgm]"
        if preserve_original_audio:
            bgm_filter += ";[0:a:0]volume=1.0000[original];"
            bgm_filter += "[original][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[audio]"
            output_audio_label = "[audio]"
        command.extend(
            [
                "-stream_loop", "-1", "-i", str(bgm),
                "-filter_complex", bgm_filter,
                "-map", "0:v:0", "-map", output_audio_label,
            ]
        )
    elif preserve_original_audio:
        command.extend(["-map", "0:v:0", "-map", "0:a?"])
    else:
        command.extend(["-map", "0:v:0", "-an"])
    expected_audio = preserve_original_audio or bgm_path is not None
    command.extend(
        [
            "-vf", ass_filter,
            *settings.output_args(preset),
        ]
    )
    if expected_audio:
        command.extend(["-c:a", "aac", "-b:a", "192k"])
    command.extend(["-movflags", "+faststart", str(target)])
    result = subprocess.run(command, cwd=subtitle.parent, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg subtitle burn failed: {(result.stderr or result.stdout or '')[-2000:]}")
    output_streams = probe_media(ffprobe, target) if target.is_file() and target.stat().st_size > 0 else {}
    if not output_streams.get("video") or bool(output_streams.get("audio")) != expected_audio:
        raise RuntimeError("Subtitle burn output validation failed.")
    return target, settings.encoder
