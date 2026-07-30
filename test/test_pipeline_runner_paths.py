from __future__ import annotations

from pathlib import Path

import videocut.pipeline.runner as runner_module
from videocut.ffmpeg_config import FFmpegVideoSettings
from videocut.pipeline.config import ParsedPipelineContext
from videocut.pipeline.runner import PipelineRunner
from videocut.pipeline.types import (
    PipelineBgmConfig,
    PipelineClipConfig,
    PipelineConfig,
    PipelineOutputConfig,
    PipelineTransitionConfig,
)


def _make_context(tmp_path: Path) -> ParsedPipelineContext:
    project_dir = tmp_path / "bgm-concat"
    project_dir.mkdir()
    config_path = project_dir / "config.json"
    return ParsedPipelineContext(
        config=PipelineConfig(
            mode="pipeline",
            name="bgm-concat",
            preset="douyin_vertical",
            quality="low",
            clips=[PipelineClipConfig()],
            output=PipelineOutputConfig(filename="final.mp4"),
            default_transition=PipelineTransitionConfig(type="cut", duration=0),
        ),
        project_dir=project_dir,
        config_path=config_path,
        resolved_srcs=[str(tmp_path / "clip.mp4")],
        junctions=[],
    )


def test_pipeline_runner_output_and_meta_are_task_unique(tmp_path, monkeypatch) -> None:
    ctx = _make_context(tmp_path)
    written_outputs: list[Path] = []

    monkeypatch.setattr(
        runner_module,
        "resolve_runtime_video_settings",
        lambda ffmpeg_path, configured: FFmpegVideoSettings(encoder="libx264"),
    )
    monkeypatch.setattr(
        runner_module,
        "probe_single_video",
        lambda ffprobe_path, video_path: {"duration": 5.0, "width": 1080, "height": 1920, "fps": 24},
    )
    monkeypatch.setattr(
        runner_module,
        "normalize_clips",
        lambda root_dir, ffmpeg_path, clips, qual_preset, res_preset, video_settings: (clips, lambda: None),
    )

    def fake_concat(ffmpeg_path, clips, junctions, output_path, qual_preset, res_preset, video_settings, task):
        output = Path(output_path)
        output.write_text("video", encoding="utf-8")
        written_outputs.append(output)

    monkeypatch.setattr(runner_module, "ffmpeg_pipeline_concat", fake_concat)

    runner = PipelineRunner(tmp_path)
    first = runner.run(ctx, "ffmpeg", "ffprobe", {}, task_id="t_first")
    second = runner.run(ctx, "ffmpeg", "ffprobe", {}, task_id="t_second")

    assert first.status == "completed"
    assert second.status == "completed"
    assert first.output_path != second.output_path
    assert Path(first.output_path).name.startswith("final_t_first_")
    assert Path(second.output_path).name.startswith("final_t_second_")
    assert len(written_outputs) == 2

    output_dir = tmp_path / "output" / "bgm-concat"
    assert not (output_dir / "meta.json").exists()
    assert len(list(output_dir.glob("meta_t_first_*.json"))) == 1
    assert len(list(output_dir.glob("meta_t_second_*.json"))) == 1


def test_pipeline_runner_random_bgm_can_use_backup_category(tmp_path, monkeypatch) -> None:
    ctx = _make_context(tmp_path)
    assert ctx.config.bgm is None
    ctx.config.bgm = PipelineBgmConfig(enabled=True, dir="input/bgm", category="legacy", volume=1.0)
    current_bgm_dir = tmp_path / "input" / "bgm"
    backup_bgm_dir = tmp_path / "input" / "bgm-backup" / "legacy"
    current_bgm_dir.mkdir(parents=True)
    backup_bgm = backup_bgm_dir / "old.mp3"
    backup_bgm_dir.mkdir(parents=True)
    backup_bgm.write_text("music", encoding="utf-8")
    applied_bgm_files: list[Path] = []

    monkeypatch.setattr(
        runner_module,
        "resolve_runtime_video_settings",
        lambda ffmpeg_path, configured: FFmpegVideoSettings(encoder="libx264"),
    )
    monkeypatch.setattr(
        runner_module,
        "probe_single_video",
        lambda ffprobe_path, video_path: {"duration": 5.0, "width": 1080, "height": 1920, "fps": 24},
    )
    monkeypatch.setattr(
        runner_module,
        "normalize_clips",
        lambda root_dir, ffmpeg_path, clips, qual_preset, res_preset, video_settings: (clips, lambda: None),
    )

    def fake_concat(ffmpeg_path, clips, junctions, output_path, qual_preset, res_preset, video_settings, task):
        Path(output_path).write_text("video", encoding="utf-8")

    def fake_apply_bgm(ffmpeg_path, ffprobe_path, video_path, bgm_file, volume, fade_out, task_id=None):
        applied_bgm_files.append(Path(bgm_file))

    monkeypatch.setattr(runner_module, "ffmpeg_pipeline_concat", fake_concat)
    monkeypatch.setattr(runner_module, "apply_bgm", fake_apply_bgm)

    result = PipelineRunner(tmp_path).run(ctx, "ffmpeg", "ffprobe", {}, task_id="t_backup_bgm")

    assert result.status == "completed"
    assert applied_bgm_files == [backup_bgm.resolve()]


def test_pipeline_runner_template_bgm_uses_template_dir_only(tmp_path, monkeypatch) -> None:
    ctx = _make_context(tmp_path)
    ctx.config.bgm = PipelineBgmConfig(
        enabled=True,
        source="template",
        category="测试1",
        filename="生活感",
        volume=1.0,
    )
    template_bgm = tmp_path / "input" / "bgm-templete" / "测试1" / "生活感.mp3"
    backup_bgm = tmp_path / "input" / "bgm-backup" / "测试1" / "生活感.mp3"
    template_bgm.parent.mkdir(parents=True)
    backup_bgm.parent.mkdir(parents=True)
    template_bgm.write_text("template music", encoding="utf-8")
    backup_bgm.write_text("backup music", encoding="utf-8")
    applied_bgm_files: list[Path] = []

    monkeypatch.setattr(
        runner_module,
        "resolve_runtime_video_settings",
        lambda ffmpeg_path, configured: FFmpegVideoSettings(encoder="libx264"),
    )
    monkeypatch.setattr(
        runner_module,
        "probe_single_video",
        lambda ffprobe_path, video_path: {"duration": 5.0, "width": 1080, "height": 1920, "fps": 24},
    )
    monkeypatch.setattr(
        runner_module,
        "normalize_clips",
        lambda root_dir, ffmpeg_path, clips, qual_preset, res_preset, video_settings: (clips, lambda: None),
    )

    def fake_concat(ffmpeg_path, clips, junctions, output_path, qual_preset, res_preset, video_settings, task):
        Path(output_path).write_text("video", encoding="utf-8")

    def fake_apply_bgm(ffmpeg_path, ffprobe_path, video_path, bgm_file, volume, fade_out, task_id=None):
        applied_bgm_files.append(Path(bgm_file))

    monkeypatch.setattr(runner_module, "ffmpeg_pipeline_concat", fake_concat)
    monkeypatch.setattr(runner_module, "apply_bgm", fake_apply_bgm)

    result = PipelineRunner(tmp_path).run(ctx, "ffmpeg", "ffprobe", {}, task_id="t_template_bgm")

    assert result.status == "completed"
    assert applied_bgm_files == [template_bgm.resolve()]


def test_pipeline_runner_resolves_avatar_bgm_from_avatar_dir_only(tmp_path) -> None:
    ctx = _make_context(tmp_path)
    ctx.config.bgm = PipelineBgmConfig(
        enabled=True,
        source="bgm-avatar",
        category="口播测试",
        filename="1",
        volume=1.0,
    )
    avatar_bgm = tmp_path / "input" / "bgm-avatar" / "口播测试" / "1.mp3"
    public_bgm = tmp_path / "input" / "bgm" / "口播测试" / "1.mp3"
    avatar_bgm.parent.mkdir(parents=True)
    public_bgm.parent.mkdir(parents=True)
    avatar_bgm.write_text("avatar", encoding="utf-8")
    public_bgm.write_text("public", encoding="utf-8")

    chosen = PipelineRunner(tmp_path).resolve_bgm_path(ctx)

    assert chosen == avatar_bgm.resolve()
