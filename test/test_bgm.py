from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import videocut.bgm as bgm_module
from videocut.bgm import apply_bgm
from videocut.bgm import resolve_bgm_file
from videocut.bgm import resolve_bgm_dir
from videocut.bgm import scan_bgm_files
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


def test_scan_bgm_files_recurses_category_directories(tmp_path) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    category_dir = bgm_dir / "20260416音乐"
    category_dir.mkdir(parents=True)
    root_audio = bgm_dir / "root.mp3"
    nested_audio = category_dir / "1.mp3"
    ignored = category_dir / "note.txt"
    root_audio.write_text("root", encoding="utf-8")
    nested_audio.write_text("nested", encoding="utf-8")
    ignored.write_text("ignored", encoding="utf-8")

    assert scan_bgm_files(bgm_dir) == sorted([root_audio, nested_audio])


def test_resolve_bgm_file_accepts_relative_file_in_category(tmp_path) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    target = bgm_dir / "20260416音乐" / "1.mp3"
    target.parent.mkdir(parents=True)
    target.write_text("music", encoding="utf-8")

    assert resolve_bgm_file(bgm_dir, "20260416音乐/1.mp3") == target


def test_resolve_bgm_file_rejects_missing_file(tmp_path) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    bgm_dir.mkdir(parents=True)

    with pytest.raises(RenderError, match="BGM file not found"):
        resolve_bgm_file(bgm_dir, "20260416音乐/missing.mp3")


def test_resolve_bgm_file_rejects_absolute_path(tmp_path) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    bgm_dir.mkdir(parents=True)

    with pytest.raises(RenderError, match="relative path"):
        resolve_bgm_file(bgm_dir, "/tmp/1.mp3")

    with pytest.raises(RenderError, match="relative path"):
        resolve_bgm_file(bgm_dir, "D:\\tmp\\1.mp3")


def test_resolve_bgm_file_rejects_parent_traversal(tmp_path) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    bgm_dir.mkdir(parents=True)

    with pytest.raises(RenderError, match="relative path"):
        resolve_bgm_file(bgm_dir, "../1.mp3")


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
