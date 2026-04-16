from dataclasses import dataclass


@dataclass(slots=True)
class ResolutionPreset:
    width: int
    height: int
    fps: int
    label: str


@dataclass(slots=True)
class QualityPreset:
    crf: int
    ffmpeg_preset: str
    label: str


AUTO_PRESET = "auto"

RESOLUTION_PRESETS: dict[str, ResolutionPreset] = {
    "douyin_vertical": ResolutionPreset(1080, 1920, 30, "Douyin vertical 1080x1920"),
    "douyin_horizontal": ResolutionPreset(1920, 1080, 30, "Douyin horizontal 1920x1080"),
    "xiaohongshu_square": ResolutionPreset(1080, 1080, 30, "Xiaohongshu square 1080x1080"),
    "xiaohongshu_vertical": ResolutionPreset(1080, 1440, 30, "Xiaohongshu vertical 1080x1440"),
    "preview": ResolutionPreset(540, 960, 24, "Preview 540x960"),
}

QUALITY_PRESETS: dict[str, QualityPreset] = {
    "low": QualityPreset(28, "fast", "Low quality"),
    "medium": QualityPreset(23, "medium", "Medium quality"),
    "high": QualityPreset(18, "slow", "High quality"),
}


def get_resolution_preset(name: str) -> ResolutionPreset:
    if name == AUTO_PRESET:
        raise ValueError('"auto" must be resolved dynamically at render time.')
    preset = RESOLUTION_PRESETS.get(name)
    if preset is None:
        available = ", ".join([AUTO_PRESET, *RESOLUTION_PRESETS.keys()])
        raise ValueError(f'Unknown resolution preset "{name}". Available: {available}')
    return preset


def get_quality_preset(name: str) -> QualityPreset:
    preset = QUALITY_PRESETS.get(name)
    if preset is None:
        available = ", ".join(QUALITY_PRESETS.keys())
        raise ValueError(f'Unknown quality preset "{name}". Available: {available}')
    return preset
