from __future__ import annotations

from pathlib import Path

import videocut.pipeline.runner as runner_module
from videocut.ffmpeg_config import FFmpegVideoSettings
from videocut.pipeline.config import ParsedPipelineContext
from videocut.pipeline.runner import PipelineRunner
from videocut.pipeline.types import (
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
