from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from videocut.oss import OssClient
from videocut.pipeline.config import parse_pipeline_config
from videocut.subtitle.ass import render_ass
from videocut.subtitle.mps import MpsClient, normalize_task_detail
from videocut.subtitle.parser import (
    SubtitleCue,
    extract_word_timed_cues,
    parse_subtitle,
    select_language,
    strip_punctuation,
    wrap_cues,
)


def _subtitle_config():
    return parse_pipeline_config(
        {
            "name": "subtitle-burn",
            "mode": "pipeline",
            "required_clip_count": 1,
            "clips": [{"source_index": 0}],
            "subtitle": {"enabled": True, "font_name": "simkai.ttf", "definition": 122},
        },
        "subtitle-burn/config.json",
        require_name=True,
    ).subtitle


def test_subtitle_config_defaults_to_simkai() -> None:
    config = _subtitle_config()
    assert config is not None
    assert config.font_name == "simkai.ttf"
    assert config.definition == 122
    assert config.need_wordlist is True
    assert config.max_chars_per_line == 10
    assert config.margin_v == 200
    assert config.strip_punctuation is True
    assert config.max_chars_per_cue == 10


def test_subtitle_config_rejects_untrusted_font_path() -> None:
    with pytest.raises(Exception, match="font_name"):
        parse_pipeline_config(
            {"name": "subtitle-burn", "clips": [{"source_index": 0}], "subtitle": {"font_name": "../evil.ttf"}},
            "config.json",
        )


def test_ass_maps_msyh_collection_to_ui_family() -> None:
    config = _subtitle_config()
    assert config is not None
    config.font_name = "msyh.ttc"
    ass = render_ass([], config)
    assert "Style: Default,Microsoft YaHei UI,40" in ass


def test_ass_uses_configured_vertical_margin() -> None:
    config = _subtitle_config()
    assert config is not None
    ass = render_ass([], config)
    assert ",2,40,40,200,1" in ass


def test_strip_punctuation_preserves_decimal_and_percent() -> None:
    cues = strip_punctuation([SubtitleCue(0, 1, ["价格3.5%，真的！"])])
    assert cues[0].lines == ["价格3.5%真的"]


def test_extract_word_timed_cues_chunks_at_ten_characters() -> None:
    wordlist = [
        {"Word": char + ("，" if index == 4 else ""), "Start": index / 10, "End": (index + 1) / 10}
        for index, char in enumerate("一二三四五六七八九十甲乙")
    ]
    response = {
        "WorkflowTask": {
            "SmartSubtitlesTaskResult": [{
                "AsrFullTextTask": {"Output": {"SegmentSet": [{"Wordlist": wordlist}]}}
            }]
        }
    }
    cues = extract_word_timed_cues(response, 10, True)
    assert [cue.lines for cue in cues] == [["一二三四五六七八九十"], ["甲乙"]]
    assert (cues[0].start, cues[0].end) == pytest.approx((0.0, 1.0))
    assert (cues[1].start, cues[1].end) == pytest.approx((1.0, 1.2))


def test_parse_select_wrap_and_render_ass() -> None:
    cues = parse_subtitle(
        "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n原文很长需要换行\nTranslated line\n"
    )
    selected = select_language(cues, "source", "auto")
    wrapped = wrap_cues(selected, 4)
    config = _subtitle_config()
    assert config is not None
    ass = render_ass(wrapped, config)
    assert "Style: Default,KaiTi,40" in ass
    assert r"原文很长\N需要换行" in ass


def test_ass_neutralizes_override_braces() -> None:
    cues = parse_subtitle("1\n00:00:00,000 --> 00:00:01,000\n{\\an8}hello\n")
    config = _subtitle_config()
    assert config is not None
    ass = render_ass(cues, config)
    assert "{\\an8}" not in ass


def test_normalize_mps_subtitle_result() -> None:
    raw = {
        "Status": "FINISH",
        "WorkflowTask": {
            "Status": "FINISH",
            "SmartSubtitlesTaskResult": [
                {"AsrFullTextTask": {"Status": "SUCCESS", "Output": {"SubtitlePath": "https://bucket.cos.ap-guangzhou.myqcloud.com/subtitle-output/a.vtt"}}}
            ],
        },
    }
    result = normalize_task_detail(
        "mps-1",
        raw,
    )
    assert result.status == "FINISH"
    assert result.subtitle_paths == ["https://bucket.cos.ap-guangzhou.myqcloud.com/subtitle-output/a.vtt"]
    assert result.raw is raw


def test_normalize_mps_failure_preserves_provider_error_with_empty_metadata() -> None:
    result = normalize_task_detail(
        "mps-failed",
        {
            "Status": "FINISH",
            "WorkflowTask": {
                "Status": "FINISH",
                "MetaData": {"AudioStreamSet": [], "VideoStreamSet": []},
                "SmartSubtitlesTaskResult": [
                    {
                        "AsrFullTextTask": {
                            "Status": "FAIL",
                            "ErrCode": 60000,
                            "ErrCodeExt": "302",
                            "Message": "Server returned 5XX Server Error reply",
                        }
                    }
                ],
            },
        },
    )

    assert result.status == "FAILED"
    assert "AsrFullTextTask" in result.message
    assert "ErrCode=60000" in result.message
    assert "ErrCodeExt=302" in result.message
    assert "Server returned 5XX Server Error reply" in result.message
    assert "Input video has no audio stream" not in result.message
    assert result.provider_status == "FAIL"
    assert result.error_code == "60000"
    assert result.error_code_ext == "302"
    assert result.provider_message == "Server returned 5XX Server Error reply"


def test_mps_wait_reports_each_polled_status(monkeypatch) -> None:
    client = MpsClient(SimpleNamespace(max_wait_seconds=60, poll_interval=0))  # type: ignore[arg-type]
    responses = iter([
        {"Status": "PROCESSING", "WorkflowTask": {"Status": "PROCESSING"}},
        {
            "Status": "FINISH",
            "WorkflowTask": {
                "Status": "FINISH",
                "SmartSubtitlesTaskResult": [
                    {"AsrFullTextTask": {"Status": "SUCCESS", "Output": {"SubtitlePath": "/subtitle.vtt"}}}
                ],
            },
        },
    ])
    monkeypatch.setattr(client, "describe", lambda task_id: next(responses))
    monkeypatch.setattr("videocut.subtitle.mps.time.sleep", lambda seconds: None)
    statuses = []

    result = client.wait("mps-poll", on_status=statuses.append)

    assert [item.provider_status for item in statuses] == ["PROCESSING", "FINISH"]
    assert result.subtitle_paths == ["/subtitle.vtt"]


def test_subtitle_oss_keys(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OSS_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("OSS_PREFIX", "GouMei-Video-Cut")
    client = OssClient()
    assert client.subtitle_input_key("sample", ".mp4") == "GouMei-Video-Cut/subtitle-input/sample.mp4"
    key = client.subtitle_output_key("t_123", datetime(2026, 7, 21, 17, 0, 0))
    assert key == "GouMei-Video-Cut/subtitle-output/20260721/20260721_170000/t_123/final.mp4"
