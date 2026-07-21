from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass


@dataclass(slots=True)
class SubtitleCue:
    start: float
    end: float
    lines: list[str]


def _seconds(raw: str) -> float:
    parts = raw.strip().replace(",", ".").split(":")
    if len(parts) == 2:
        hours, minutes, seconds = 0, parts[0], parts[1]
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise ValueError(f"Unsupported subtitle timestamp: {raw}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_subtitle(text: str) -> list[SubtitleCue]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: list[SubtitleCue] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip().lstrip("\ufeff")
        if not line or line.upper() == "WEBVTT":
            index += 1
            continue
        if line.startswith("NOTE"):
            index += 1
            while index < len(lines) and lines[index].strip():
                index += 1
            continue
        if "-->" not in line:
            if index + 1 < len(lines) and "-->" in lines[index + 1]:
                index += 1
                line = lines[index].strip()
            else:
                index += 1
                continue
        start_raw, end_raw = [part.strip() for part in line.split("-->", 1)]
        end_raw = end_raw.split()[0]
        index += 1
        cue_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            cue_lines.append(re.sub(r"<[^>]+>", "", lines[index].rstrip()))
            index += 1
        start, end = _seconds(start_raw), _seconds(end_raw)
        if cue_lines and start >= 0 and end > start:
            cues.append(SubtitleCue(start, end, cue_lines))
    if not cues:
        raise ValueError("Subtitle file contains no usable cues.")
    return cues


def select_language(cues: list[SubtitleCue], mode: str, target_language: str) -> list[SubtitleCue]:
    if mode == "bilingual":
        return cues
    line_index = -1 if mode == "translation" or (mode == "auto" and target_language.lower() != "auto") else 0
    return [SubtitleCue(cue.start, cue.end, [cue.lines[line_index]]) for cue in cues if cue.lines]


def wrap_cues(cues: list[SubtitleCue], width: int) -> list[SubtitleCue]:
    wrapped: list[SubtitleCue] = []
    for cue in cues:
        lines: list[str] = []
        for line in cue.lines:
            lines.extend(textwrap.wrap(line, width=width, break_long_words=True, break_on_hyphens=False) or [""])
        wrapped.append(SubtitleCue(cue.start, cue.end, lines))
    return wrapped
