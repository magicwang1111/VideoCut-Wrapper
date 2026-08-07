from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import videocut.bgm as bgm_module
from videocut.bgm import OriginalAudioSegment, apply_bgm
from videocut.bgm import build_bgm_manifest
from videocut.bgm import list_bgm_catalog
from videocut.bgm import resolve_bgm_backup_dir
from videocut.bgm import resolve_bgm_category_dir_optional
from videocut.bgm import resolve_bgm_category_file
from videocut.bgm import resolve_bgm_dir
from videocut.bgm import scan_bgm_category_files
from videocut.bgm import scan_bgm_files
from videocut.bgm import write_bgm_manifest
from videocut.errors import RenderError


def test_resolve_bgm_dir_defaults_to_repo_input_bgm(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BGM_DIR", raising=False)
    result = resolve_bgm_dir(tmp_path)
    assert result == tmp_path / "input" / "bgm"


def test_resolve_bgm_dir_prefers_env_and_resolves_relative_to_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BGM_DIR", "runtime/bgm")
    result = resolve_bgm_dir(tmp_path, "custom/bgm")
    assert result == tmp_path / "runtime" / "bgm"


def test_resolve_bgm_dir_uses_configured_relative_path_without_env(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BGM_DIR", raising=False)
    result = resolve_bgm_dir(tmp_path, "custom/bgm")
    assert result == tmp_path / "custom" / "bgm"


def test_resolve_bgm_backup_dir_defaults_to_repo_input_bgm_backup(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BGM_BACKUP_DIR", raising=False)
    result = resolve_bgm_backup_dir(tmp_path)
    assert result == tmp_path / "input" / "bgm-backup"


def test_resolve_bgm_backup_dir_prefers_env_and_resolves_relative_to_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BGM_BACKUP_DIR", "runtime/bgm-backup")
    result = resolve_bgm_backup_dir(tmp_path)
    assert result == tmp_path / "runtime" / "bgm-backup"


def test_scan_bgm_files_recurses_category_directories(tmp_path) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    category_dir = bgm_dir / "舒缓"
    category_dir.mkdir(parents=True)
    root_audio = bgm_dir / "root.mp3"
    nested_audio = category_dir / "1.mp3"
    ignored = category_dir / "note.txt"
    root_audio.write_text("root", encoding="utf-8")
    nested_audio.write_text("nested", encoding="utf-8")
    ignored.write_text("ignored", encoding="utf-8")

    assert scan_bgm_files(bgm_dir) == sorted([root_audio, nested_audio])


def test_scan_bgm_category_files_limits_random_pool_to_category(tmp_path) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    calm_dir = bgm_dir / "舒缓"
    intense_dir = bgm_dir / "激烈"
    calm_nested = calm_dir / "nested"
    calm_nested.mkdir(parents=True)
    intense_dir.mkdir(parents=True)
    calm_audio = calm_dir / "1.mp3"
    calm_nested_audio = calm_nested / "2.wav"
    intense_audio = intense_dir / "3.mp3"
    ignored = calm_dir / "note.txt"
    calm_audio.write_text("calm", encoding="utf-8")
    calm_nested_audio.write_text("nested", encoding="utf-8")
    intense_audio.write_text("intense", encoding="utf-8")
    ignored.write_text("ignored", encoding="utf-8")

    assert scan_bgm_category_files(bgm_dir, "舒缓") == sorted([calm_audio, calm_nested_audio])


def test_list_bgm_catalog_uses_relative_category_paths_and_plain_filenames(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BGM_OSS_URI", raising=False)
    monkeypatch.delenv("OSS_PUBLIC_ENDPOINT", raising=False)
    bgm_dir = tmp_path / "input" / "bgm"
    calm_dir = bgm_dir / "calm"
    nested_dir = calm_dir / "nested"
    intense_dir = bgm_dir / "intense"
    nested_dir.mkdir(parents=True)
    intense_dir.mkdir(parents=True)
    (calm_dir / "1.mp3").write_text("calm", encoding="utf-8")
    (calm_dir / "测试1.mp3").write_text("calm-cn", encoding="utf-8")
    (nested_dir / "2.wav").write_text("nested", encoding="utf-8")
    (intense_dir / "3.flac").write_text("intense", encoding="utf-8")
    (intense_dir / "note.txt").write_text("ignored", encoding="utf-8")

    assert list_bgm_catalog(bgm_dir) == {
        "bgmRoot": str(bgm_dir.resolve()),
        "categories": [
            {"name": "calm", "displayName": "舒缓", "count": 2},
            {"name": "calm/nested", "displayName": "舒缓/nested", "count": 1},
            {"name": "intense", "displayName": "激烈", "count": 1},
        ],
        "files": [
            {
                "category": "calm",
                "displayName": "舒缓",
                "filename": "1",
                "ossUrl": "https://goumee-coze.oss-cn-hangzhou.aliyuncs.com/GouMei-Video-Cut/bgm/calm/1.mp3",
            },
            {
                "category": "calm/nested",
                "displayName": "舒缓/nested",
                "filename": "2",
                "ossUrl": "https://goumee-coze.oss-cn-hangzhou.aliyuncs.com/GouMei-Video-Cut/bgm/calm/nested/2.wav",
            },
            {
                "category": "calm",
                "displayName": "舒缓",
                "filename": "测试1",
                "ossUrl": "https://goumee-coze.oss-cn-hangzhou.aliyuncs.com/GouMei-Video-Cut/bgm/calm/%E6%B5%8B%E8%AF%951.mp3",
            },
            {
                "category": "intense",
                "displayName": "激烈",
                "filename": "3",
                "ossUrl": "https://goumee-coze.oss-cn-hangzhou.aliyuncs.com/GouMei-Video-Cut/bgm/intense/3.flac",
            },
        ],
    }


def test_list_bgm_catalog_uses_configured_oss_uri_without_trailing_slash(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BGM_OSS_URI", raising=False)
    monkeypatch.delenv("OSS_PUBLIC_ENDPOINT", raising=False)
    bgm_dir = tmp_path / "input" / "bgm"
    (bgm_dir / "calm").mkdir(parents=True)
    (bgm_dir / "calm" / "1.mp3").write_text("calm", encoding="utf-8")

    catalog = list_bgm_catalog(bgm_dir, oss_uri_base="oss://bucket/custom/bgm")

    assert catalog["files"] == [
        {
            "category": "calm",
            "displayName": "舒缓",
            "filename": "1",
            "ossUrl": "https://bucket.oss-cn-hangzhou.aliyuncs.com/custom/bgm/calm/1.mp3",
        }
    ]


def test_list_bgm_catalog_uses_bgm_oss_uri_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BGM_OSS_URI", "oss://bucket/env-bgm/")
    monkeypatch.delenv("OSS_PUBLIC_ENDPOINT", raising=False)
    bgm_dir = tmp_path / "input" / "bgm"
    (bgm_dir / "calm").mkdir(parents=True)
    (bgm_dir / "calm" / "1.mp3").write_text("calm", encoding="utf-8")

    catalog = list_bgm_catalog(bgm_dir, oss_uri_base="oss://bucket/configured")

    assert catalog["files"] == [
        {
            "category": "calm",
            "displayName": "舒缓",
            "filename": "1",
            "ossUrl": "https://bucket.oss-cn-hangzhou.aliyuncs.com/env-bgm/calm/1.mp3",
        }
    ]


def test_build_bgm_manifest_uses_api_root_and_generated_source(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BGM_OSS_URI", raising=False)
    monkeypatch.delenv("OSS_PUBLIC_ENDPOINT", raising=False)
    bgm_dir = tmp_path / "input" / "bgm"
    (bgm_dir / "calm").mkdir(parents=True)
    (bgm_dir / "calm" / "1.mp3").write_text("calm", encoding="utf-8")

    assert build_bgm_manifest(bgm_dir, api_bgm_root="/app/input/bgm") == {
        "bgmRoot": "/app/input/bgm",
        "pathRule": (
            "API overrides.bgm.category + overrides.bgm.filename uses the category and extensionless filename fields below, "
            "relative to /app/input/bgm."
        ),
        "generatedFrom": str(bgm_dir.resolve()),
        "categories": [{"name": "calm", "displayName": "舒缓", "count": 1}],
        "files": [
            {
                "category": "calm",
                "displayName": "舒缓",
                "filename": "1",
                "ossUrl": "https://goumee-coze.oss-cn-hangzhou.aliyuncs.com/GouMei-Video-Cut/bgm/calm/1.mp3",
            }
        ],
    }


def test_write_bgm_manifest_writes_utf8_json(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BGM_OSS_URI", raising=False)
    monkeypatch.delenv("OSS_PUBLIC_ENDPOINT", raising=False)
    bgm_dir = tmp_path / "input" / "bgm"
    output_path = tmp_path / "docs" / "BGM_MANIFEST.json"
    (bgm_dir / "calm").mkdir(parents=True)
    (bgm_dir / "calm" / "1.mp3").write_text("calm", encoding="utf-8")

    manifest = write_bgm_manifest(bgm_dir, output_path)

    assert output_path.read_text(encoding="utf-8").endswith("\n")
    assert '"displayName": "舒缓"' in output_path.read_text(encoding="utf-8")
    assert manifest["files"] == [
        {
            "category": "calm",
            "displayName": "舒缓",
            "filename": "1",
            "ossUrl": "https://goumee-coze.oss-cn-hangzhou.aliyuncs.com/GouMei-Video-Cut/bgm/calm/1.mp3",
        }
    ]


def test_scan_bgm_category_files_rejects_missing_category(tmp_path) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    bgm_dir.mkdir(parents=True)

    with pytest.raises(RenderError, match="BGM category directory not found"):
        scan_bgm_category_files(bgm_dir, "舒缓")


def test_resolve_bgm_category_dir_optional_returns_existing_category(tmp_path) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    category_dir = bgm_dir / "catalog"
    category_dir.mkdir(parents=True)

    assert resolve_bgm_category_dir_optional(bgm_dir, "catalog") == category_dir
    assert resolve_bgm_category_dir_optional(bgm_dir, "missing") is None


def test_resolve_bgm_category_dir_optional_reuses_category_path_validation(tmp_path) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    bgm_dir.mkdir(parents=True)

    with pytest.raises(RenderError, match="relative directory"):
        resolve_bgm_category_dir_optional(bgm_dir, "../outside")


def test_scan_bgm_category_files_rejects_category_without_audio(tmp_path) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    category_dir = bgm_dir / "舒缓"
    category_dir.mkdir(parents=True)
    (category_dir / "note.txt").write_text("ignored", encoding="utf-8")

    with pytest.raises(RenderError, match="No audio files found"):
        scan_bgm_category_files(bgm_dir, "舒缓")


@pytest.mark.parametrize("category", ["/tmp", "D:\\tmp", "../舒缓", "./舒缓", ".", "舒缓/../激烈", "舒缓/."])
def test_scan_bgm_category_files_rejects_unsafe_category_paths(tmp_path, category: str) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    bgm_dir.mkdir(parents=True)

    with pytest.raises(RenderError, match="relative directory"):
        scan_bgm_category_files(bgm_dir, category)


def test_resolve_bgm_category_file_accepts_category_and_filename(tmp_path) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    target = bgm_dir / "舒缓" / "1.mp3"
    target.parent.mkdir(parents=True)
    target.write_text("music", encoding="utf-8")

    assert resolve_bgm_category_file(bgm_dir, "舒缓", "1") == target


def test_resolve_bgm_category_file_falls_back_to_backup_dir(tmp_path) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    backup_dir = tmp_path / "input" / "bgm-backup"
    target = backup_dir / "legacy" / "1.mp3"
    bgm_dir.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    target.write_text("music", encoding="utf-8")

    assert resolve_bgm_category_file(bgm_dir, "legacy", "1", backup_bgm_dir=backup_dir) == target


def test_resolve_bgm_category_file_prefers_current_dir_over_backup_dir(tmp_path) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    backup_dir = tmp_path / "input" / "bgm-backup"
    current = bgm_dir / "legacy" / "1.mp3"
    backup = backup_dir / "legacy" / "1.mp3"
    current.parent.mkdir(parents=True)
    backup.parent.mkdir(parents=True)
    current.write_text("current", encoding="utf-8")
    backup.write_text("backup", encoding="utf-8")

    assert resolve_bgm_category_file(bgm_dir, "legacy", "1", backup_bgm_dir=backup_dir) == current


def test_resolve_bgm_category_file_reports_primary_and_backup_paths_when_missing(tmp_path) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    backup_dir = tmp_path / "input" / "bgm-backup"
    bgm_dir.mkdir(parents=True)
    backup_dir.mkdir(parents=True)

    with pytest.raises(RenderError, match="backup checked"):
        resolve_bgm_category_file(bgm_dir, "legacy", "1", backup_bgm_dir=backup_dir)


@pytest.mark.parametrize("filename", ["/tmp/1", "D:\\tmp\\1", "../1", "./1", ".", "nested/1", "1.mp3"])
def test_resolve_bgm_category_file_rejects_unsafe_filenames(tmp_path, filename: str) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    category_dir = bgm_dir / "舒缓"
    category_dir.mkdir(parents=True)

    with pytest.raises(RenderError, match="extensionless plain file name"):
        resolve_bgm_category_file(bgm_dir, "舒缓", filename)


def test_resolve_bgm_category_file_rejects_duplicate_stems(tmp_path) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    category_dir = bgm_dir / "舒缓"
    category_dir.mkdir(parents=True)
    (category_dir / "1.mp3").write_text("music", encoding="utf-8")
    (category_dir / "1.wav").write_text("music", encoding="utf-8")

    with pytest.raises(RenderError, match="Duplicate BGM filename stem"):
        resolve_bgm_category_file(bgm_dir, "舒缓", "1")


def test_list_bgm_catalog_rejects_duplicate_stems_in_same_category(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BGM_OSS_URI", raising=False)
    bgm_dir = tmp_path / "input" / "bgm"
    category_dir = bgm_dir / "calm"
    category_dir.mkdir(parents=True)
    (category_dir / "1.mp3").write_text("music", encoding="utf-8")
    (category_dir / "1.wav").write_text("music", encoding="utf-8")

    with pytest.raises(RenderError, match="Duplicate BGM filename stem"):
        list_bgm_catalog(bgm_dir)


def test_list_bgm_catalog_rejects_stems_with_dots(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BGM_OSS_URI", raising=False)
    bgm_dir = tmp_path / "input" / "bgm"
    category_dir = bgm_dir / "calm"
    category_dir.mkdir(parents=True)
    (category_dir / "song.v1.mp3").write_text("music", encoding="utf-8")

    with pytest.raises(RenderError, match="extensionless plain file name"):
        list_bgm_catalog(bgm_dir)


def test_apply_bgm_uses_task_unique_tmp_and_replaces_video(tmp_path, monkeypatch) -> None:
    video = tmp_path / "final.mp4"
    video.write_text("without bgm", encoding="utf-8")
    bgm_file = tmp_path / "music.mp3"
    bgm_file.write_text("music", encoding="utf-8")
    written_tmp_paths: list[Path] = []

    monkeypatch.setattr(
        bgm_module.subprocess,
        "check_output",
        lambda *args, **kwargs: '{"format": {"duration": "5.0"}}',
    )

    def fake_run(args, **kwargs):
        tmp_path_arg = Path(args[-1])
        written_tmp_paths.append(tmp_path_arg)
        tmp_path_arg.write_text("with bgm", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(bgm_module.subprocess, "run", fake_run)

    apply_bgm("ffmpeg", "ffprobe", str(video), bgm_file, 0.3, 1.0, "t_demo")

    assert video.read_text(encoding="utf-8") == "with bgm"
    assert len(written_tmp_paths) == 1
    assert written_tmp_paths[0].name.startswith("final.mp4.t_demo.")
    assert written_tmp_paths[0].name.endswith(".bgm_tmp.mp4")
    assert not list(tmp_path.glob("*.bgm_tmp.mp4"))


def test_apply_bgm_concatenates_original_audio_and_silence_before_mixing(tmp_path, monkeypatch) -> None:
    video = tmp_path / "final.mp4"
    video.write_text("video", encoding="utf-8")
    first = tmp_path / "first.mp4"
    first.write_text("first", encoding="utf-8")
    third = tmp_path / "third.mp4"
    third.write_text("third", encoding="utf-8")
    bgm_file = tmp_path / "music.mp3"
    bgm_file.write_text("music", encoding="utf-8")
    captured_args: list[str] = []

    monkeypatch.setattr(
        bgm_module.subprocess,
        "check_output",
        lambda *args, **kwargs: '{"format": {"duration": "12.0"}}',
    )

    def fake_run(args, **kwargs):
        captured_args.extend(args)
        Path(args[-1]).write_text("mixed", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(bgm_module.subprocess, "run", fake_run)

    apply_bgm(
        "ffmpeg",
        "ffprobe",
        str(video),
        bgm_file,
        0.3,
        1.0,
        "t_demo",
        original_audio_segments=[
            OriginalAudioSegment(str(first), trim_start=1.0, duration=4.0, has_audio=True),
            OriginalAudioSegment(str(tmp_path / "silent.mp4"), trim_start=0.0, duration=3.0, has_audio=False),
            OriginalAudioSegment(str(third), trim_start=2.0, duration=5.0, has_audio=True),
        ],
    )

    filter_complex = captured_args[captured_args.index("-filter_complex") + 1]
    assert captured_args.count("-i") == 4
    assert "[1:a:0]atrim=start=1.000000:duration=4.000000" in filter_complex
    assert "anullsrc=r=48000:cl=stereo,atrim=duration=3.000000" in filter_complex
    assert "[2:a:0]atrim=start=2.000000:duration=5.000000" in filter_complex
    assert "[original0][original1][original2]concat=n=3:v=0:a=1" in filter_complex
    assert "[original][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[audio]" in filter_complex
    assert captured_args[captured_args.index("-map", captured_args.index("-map") + 1) + 1] == "[audio]"


def test_apply_bgm_uses_bgm_only_when_all_input_segments_are_silent(tmp_path, monkeypatch) -> None:
    video = tmp_path / "final.mp4"
    video.write_text("video", encoding="utf-8")
    bgm_file = tmp_path / "music.mp3"
    bgm_file.write_text("music", encoding="utf-8")
    captured_args: list[str] = []

    monkeypatch.setattr(
        bgm_module.subprocess,
        "check_output",
        lambda *args, **kwargs: '{"format": {"duration": "5.0"}}',
    )

    def fake_run(args, **kwargs):
        captured_args.extend(args)
        Path(args[-1]).write_text("bgm only", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(bgm_module.subprocess, "run", fake_run)

    apply_bgm(
        "ffmpeg",
        "ffprobe",
        str(video),
        bgm_file,
        0.3,
        0.0,
        "t_demo",
        original_audio_segments=[
            OriginalAudioSegment(str(tmp_path / "first.mp4"), 0.0, 2.0, False),
            OriginalAudioSegment(str(tmp_path / "second.mp4"), 0.0, 3.0, False),
        ],
    )

    filter_complex = captured_args[captured_args.index("-filter_complex") + 1]
    assert captured_args.count("-i") == 2
    assert "anullsrc" not in filter_complex
    assert "amix=" not in filter_complex
    assert captured_args[captured_args.index("-map", captured_args.index("-map") + 1) + 1] == "[bgm]"


def test_apply_bgm_cleans_task_unique_tmp_on_failure(tmp_path, monkeypatch) -> None:
    video = tmp_path / "final.mp4"
    video.write_text("without bgm", encoding="utf-8")
    bgm_file = tmp_path / "music.mp3"
    bgm_file.write_text("music", encoding="utf-8")

    monkeypatch.setattr(
        bgm_module.subprocess,
        "check_output",
        lambda *args, **kwargs: '{"format": {"duration": "5.0"}}',
    )

    def fake_run(args, **kwargs):
        tmp_path_arg = Path(args[-1])
        tmp_path_arg.write_text("partial", encoding="utf-8")
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(bgm_module.subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        apply_bgm("ffmpeg", "ffprobe", str(video), bgm_file, 0.3, 1.0, "t_demo")

    assert video.read_text(encoding="utf-8") == "without bgm"
    assert not list(tmp_path.glob("*.bgm_tmp.mp4"))
