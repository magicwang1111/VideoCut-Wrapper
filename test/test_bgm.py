from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import videocut.bgm as bgm_module
from videocut.bgm import apply_bgm
from videocut.bgm import build_bgm_manifest
from videocut.bgm import list_bgm_catalog
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


def test_list_bgm_catalog_uses_relative_category_paths_and_plain_filenames(tmp_path) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    calm_dir = bgm_dir / "舒缓"
    nested_dir = calm_dir / "nested"
    intense_dir = bgm_dir / "激烈"
    nested_dir.mkdir(parents=True)
    intense_dir.mkdir(parents=True)
    (calm_dir / "1.mp3").write_text("calm", encoding="utf-8")
    (nested_dir / "2.wav").write_text("nested", encoding="utf-8")
    (intense_dir / "3.flac").write_text("intense", encoding="utf-8")
    (intense_dir / "note.txt").write_text("ignored", encoding="utf-8")

    assert list_bgm_catalog(bgm_dir) == {
        "bgmRoot": str(bgm_dir.resolve()),
        "categories": [
            {"name": "激烈", "count": 1},
            {"name": "舒缓", "count": 1},
            {"name": "舒缓/nested", "count": 1},
        ],
        "files": [
            {"category": "激烈", "filename": "3.flac"},
            {"category": "舒缓", "filename": "1.mp3"},
            {"category": "舒缓/nested", "filename": "2.wav"},
        ],
    }


def test_build_bgm_manifest_uses_api_root_and_generated_source(tmp_path) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    (bgm_dir / "舒缓").mkdir(parents=True)
    (bgm_dir / "舒缓" / "1.mp3").write_text("calm", encoding="utf-8")

    assert build_bgm_manifest(bgm_dir, api_bgm_root="/app/input/bgm") == {
        "bgmRoot": "/app/input/bgm",
        "pathRule": (
            "API overrides.bgm.category + overrides.bgm.filename uses the category and filename fields below, "
            "relative to /app/input/bgm."
        ),
        "generatedFrom": str(bgm_dir.resolve()),
        "categories": [{"name": "舒缓", "count": 1}],
        "files": [{"category": "舒缓", "filename": "1.mp3"}],
    }


def test_write_bgm_manifest_writes_utf8_json(tmp_path) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    output_path = tmp_path / "docs" / "BGM_MANIFEST.json"
    (bgm_dir / "舒缓").mkdir(parents=True)
    (bgm_dir / "舒缓" / "1.mp3").write_text("calm", encoding="utf-8")

    manifest = write_bgm_manifest(bgm_dir, output_path)

    assert output_path.read_text(encoding="utf-8").endswith("\n")
    assert '"category": "舒缓"' in output_path.read_text(encoding="utf-8")
    assert manifest["files"] == [{"category": "舒缓", "filename": "1.mp3"}]


def test_scan_bgm_category_files_rejects_missing_category(tmp_path) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    bgm_dir.mkdir(parents=True)

    with pytest.raises(RenderError, match="BGM category directory not found"):
        scan_bgm_category_files(bgm_dir, "舒缓")


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

    assert resolve_bgm_category_file(bgm_dir, "舒缓", "1.mp3") == target


@pytest.mark.parametrize("filename", ["/tmp/1.mp3", "D:\\tmp\\1.mp3", "../1.mp3", "./1.mp3", ".", "nested/1.mp3"])
def test_resolve_bgm_category_file_rejects_unsafe_filenames(tmp_path, filename: str) -> None:
    bgm_dir = tmp_path / "input" / "bgm"
    category_dir = bgm_dir / "舒缓"
    category_dir.mkdir(parents=True)

    with pytest.raises(RenderError, match="plain file name"):
        resolve_bgm_category_file(bgm_dir, "舒缓", filename)


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
