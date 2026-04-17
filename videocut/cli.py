from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

import typer
import uvicorn

from videocut.errors import VideoCutError
from videocut.ffmpeg_config import resolve_runtime_video_settings, resolve_video_settings
from videocut.log import setup_logging
from videocut.pipeline import PipelineRunner, resolve_pipeline_config
from videocut.presets import AUTO_PRESET, QUALITY_PRESETS, RESOLUTION_PRESETS, get_resolution_preset
from videocut.render import resolve_ffmpeg_path, resolve_ffprobe_path

app = typer.Typer(add_completion=False, no_args_is_help=True)

ROOT_DIR = Path(__file__).resolve().parents[1]
PIPELINES_DIR = ROOT_DIR / "pipelines"


@app.command()
def pipelines() -> None:
    from videocut.config import REGISTERED_PIPELINES
    if not REGISTERED_PIPELINES:
        typer.echo("No registered pipelines.")
        raise typer.Exit()
    typer.echo(f"\nRegistered pipelines ({len(REGISTERED_PIPELINES)}):\n")
    for name, info in sorted(REGISTERED_PIPELINES.items()):
        typer.echo(f"  {name:30}  {info['source_path']}")
    typer.echo("")


@app.command()
def presets() -> None:
    typer.echo("\nResolution presets:\n")
    typer.echo(f"{AUTO_PRESET:24}auto-detect from the first video clip")
    for name, preset in RESOLUTION_PRESETS.items():
        typer.echo(f"{name:24}{preset.width}x{preset.height} {preset.fps}fps  {preset.label}")
    typer.echo("\nQuality presets:\n")
    for name, preset in QUALITY_PRESETS.items():
        typer.echo(f"{name:10}CRF {preset.crf:<3} {preset.label}")
    typer.echo("")


@app.command()
def check() -> None:
    typer.echo("\nSystem dependency check\n")
    typer.echo(f"  Python: {subprocess.check_output(['python', '--version'], encoding='utf-8').strip()}")
    ffmpeg_path = resolve_ffmpeg_path(ROOT_DIR)
    ffprobe_path = resolve_ffprobe_path(ROOT_DIR)
    if ffmpeg_path:
        version = subprocess.check_output([ffmpeg_path, "-version"], encoding="utf-8").splitlines()[0]
        typer.echo(f"  FFmpeg: {version}")
        video_settings = resolve_runtime_video_settings(ffmpeg_path, resolve_video_settings())
        typer.echo(f"  Video encoder: {video_settings.encoder}")
        if video_settings.hwaccel:
            typer.echo(f"  HWAccel: {video_settings.hwaccel}")
    else:
        typer.echo("  FFmpeg: not found")
    typer.echo(f"  ffprobe: {ffprobe_path or 'not found'}")
    fonts_dir = ROOT_DIR / "fonts"
    if fonts_dir.exists():
        fonts = [path.name for path in fonts_dir.iterdir() if path.suffix.lower() in {'.otf', '.ttf', '.woff', '.woff2'}]
        typer.echo(f"  Fonts: {len(fonts)} ({', '.join(fonts) if fonts else 'none'})")
    typer.echo("")


@app.command()
def render(
    pipeline: str = typer.Argument(..., help="Pipeline name"),
    preset: Optional[str] = typer.Option(None),
    quality: Optional[str] = typer.Option(None),
    output: Optional[str] = typer.Option(None),
    preview: bool = typer.Option(False),
) -> None:
    try:
        pipeline_files = list(PIPELINES_DIR.rglob("*.yaml")) + list(PIPELINES_DIR.rglob("*.yml"))
        matched = [p for p in pipeline_files if p.stem == pipeline or p.name == pipeline]
        if not matched:
            typer.echo(f"\nPipeline not found: {pipeline}\n", err=True)
            raise typer.Exit(code=1)
        ctx = resolve_pipeline_config(str(matched[0]))
        overrides: dict[str, str] = {}
        if preset:
            overrides["preset"] = preset
        if quality:
            overrides["quality"] = quality
        if preview:
            overrides["preset"] = "preview"
        ffmpeg_path = resolve_ffmpeg_path(ROOT_DIR)
        ffprobe_path = resolve_ffprobe_path(ROOT_DIR)
        if not ffmpeg_path or not ffprobe_path:
            typer.echo("\nPipeline mode requires FFmpeg and ffprobe.\n", err=True)
            raise typer.Exit(code=1)
        typer.echo("\nStart render (pipeline mode)")
        resolved_preset = overrides.get("preset") or ctx.config.preset or "auto"
        if resolved_preset == AUTO_PRESET:
            typer.echo("  Preset: auto")
        else:
            preset_info = get_resolution_preset(resolved_preset)
            typer.echo(f"  Preset: {resolved_preset} ({preset_info.width}x{preset_info.height})")
        typer.echo(f"  Quality: {overrides.get('quality') or ctx.config.quality or 'high'}")
        result = PipelineRunner(ROOT_DIR).run(ctx, ffmpeg_path, ffprobe_path, overrides)
        if result.status == "completed":
            typer.echo("\nRender completed")
            typer.echo(f"  Output: {result.output_path}")
            if result.duration is not None:
                typer.echo(f"  Elapsed: {result.duration:.1f}s")
            typer.echo("")
            return
        typer.echo(f"\nRender failed: {result.error}\n", err=True)
        raise typer.Exit(code=1)
    except VideoCutError as exc:
        typer.secho(f"\n[E{exc.code}] {exc}\n", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


@app.command("serve")
def serve(host: str = "0.0.0.0", port: int = 3000) -> None:
    uvicorn.run("videocut.api.app:app", host=host, port=port, reload=False)


def main() -> None:
    setup_logging()
    app()


if __name__ == "__main__":
    main()
