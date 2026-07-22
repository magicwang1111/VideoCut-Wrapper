from __future__ import annotations

import re
import textwrap
import unicodedata
from dataclasses import dataclass
from typing import Any


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


def strip_punctuation_text(text: str) -> str:
    return "".join(
        char
        for index, char in enumerate(text)
        if char in {"%", "％"}
        or not unicodedata.category(char).startswith("P")
        or (char in {".", "．"} and index > 0 and index + 1 < len(text)
            and text[index - 1].isdigit() and text[index + 1].isdigit())
    )


def strip_punctuation(cues: list[SubtitleCue]) -> list[SubtitleCue]:
    return [
        SubtitleCue(cue.start, cue.end, [strip_punctuation_text(line) for line in cue.lines])
        for cue in cues
    ]


def extract_word_timed_cues(
    response: dict[str, Any], max_chars_per_cue: int, remove_punctuation: bool
) -> list[SubtitleCue]:
    workflow = response.get("WorkflowTask")
    if not isinstance(workflow, dict):
        return []

    selected_segments: list[dict[str, Any]] | None = None
    for subtitle_task in workflow.get("SmartSubtitlesTaskResult") or []:
        if not isinstance(subtitle_task, dict):
            continue
        for task_name in ("AsrFullTextTask", "TransTextTask", "PureSubtitleTransTask", "OcrFullTextTask"):
            task = subtitle_task.get(task_name)
            output = task.get("Output") if isinstance(task, dict) else None
            segments = output.get("SegmentSet") if isinstance(output, dict) else None
            if isinstance(segments, list) and any(
                isinstance(segment, dict) and segment.get("Wordlist") for segment in segments
            ):
                selected_segments = segments
                break
        if selected_segments is not None:
            break
    if selected_segments is None:
        return []

    cues: list[SubtitleCue] = []
    for segment in selected_segments:
        wordlist = segment.get("Wordlist") if isinstance(segment, dict) else None
        if not isinstance(wordlist, list) or not wordlist:
            continue
        timed_chars: list[tuple[str, float, float]] = []
        for word_item in wordlist:
            if not isinstance(word_item, dict):
                continue
            word = str(word_item.get("Word") or "")
            if remove_punctuation:
                word = strip_punctuation_text(word)
            if not word:
                continue
            try:
                start = float(word_item["Start"])
                end = float(word_item["End"])
            except (KeyError, TypeError, ValueError):
                continue
            duration = max(0.0, end - start)
            for index, char in enumerate(word):
                timed_chars.append((
                    char,
                    start + duration * index / len(word),
                    start + duration * (index + 1) / len(word),
                ))

        if not remove_punctuation and timed_chars:
            restored: list[tuple[str, float, float]] = []
            word_index = 0
            for char in str(segment.get("Text") or ""):
                if word_index < len(timed_chars) and char == timed_chars[word_index][0]:
                    restored.append(timed_chars[word_index])
                    word_index += 1
                elif char.isspace() or unicodedata.category(char).startswith("P"):
                    anchor = restored[-1][2] if restored else timed_chars[0][1]
                    restored.append((char, anchor, anchor))
            restored.extend(timed_chars[word_index:])
            timed_chars = restored

        for index in range(0, len(timed_chars), max_chars_per_cue):
            chunk = timed_chars[index:index + max_chars_per_cue]
            if chunk:
                cues.append(SubtitleCue(
                    chunk[0][1], chunk[-1][2], ["".join(item[0] for item in chunk)]
                ))
    return cues
