from __future__ import annotations

from pathlib import Path

from videocut.pipeline.types import PipelineSubtitleConfig
from videocut.subtitle.parser import SubtitleCue

_ALIGNMENT = {"bottom-left": 1, "bottom": 2, "bottom-right": 3, "middle": 5,
              "top-left": 7, "top": 8, "top-right": 9}
_FONT_FAMILIES = {"simkai.ttf": "KaiTi"}


def _timestamp(seconds: float) -> str:
    centiseconds = int(round(seconds * 100))
    hours, centiseconds = divmod(centiseconds, 360000)
    minutes, centiseconds = divmod(centiseconds, 6000)
    secs, centis = divmod(centiseconds, 100)
    return f"{hours}:{minutes:02}:{secs:02}.{centis:02}"


def _color(rgb: str, opacity: float) -> str:
    raw = rgb.lstrip("#")
    alpha = int(round((1.0 - opacity) * 255))
    return f"&H{alpha:02X}{raw[4:6]}{raw[2:4]}{raw[0:2]}".upper()


def render_ass(cues: list[SubtitleCue], config: PipelineSubtitleConfig) -> str:
    font_family = _FONT_FAMILIES.get(config.font_name.lower(), config.font_name)
    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1920", "PlayResY: 1080",
        f"WrapStyle: {0 if config.auto_wrap else 2}", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        f"Style: Default,{font_family},{config.font_size},{_color(config.font_color, config.font_alpha)},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,{_ALIGNMENT[config.position]},40,40,40,1",
        "", "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    for cue in cues:
        text = r"\N".join(line.replace("\\", r"\\").replace("{", "(").replace("}", ")") for line in cue.lines)
        lines.append(f"Dialogue: 0,{_timestamp(cue.start)},{_timestamp(cue.end)},Default,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"


def write_ass(path: str | Path, cues: list[SubtitleCue], config: PipelineSubtitleConfig) -> Path:
    target = Path(path)
    target.write_text(render_ass(cues, config), encoding="utf-8-sig")
    return target
