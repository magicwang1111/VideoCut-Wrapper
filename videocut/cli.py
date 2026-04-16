from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

import typer
import uvicorn

from videocut.errors import VideoCutError
from videocut.pipeline import PipelineRunner, is_pipeline_config, load_raw_yaml, resolve_pipeline_config
from videocut.presets import AUTO_PRESET, QUALITY_PRESETS, RESOLUTION_PRESETS, get_resolution_preset
from videocut.project import ProjectManager
from videocut.registry import TemplateRegistry
from videocut.render import RenderService, resolve_ffmpeg_path, resolve_ffprobe_path
from videocut.render.types import RenderRequest

app = typer.Typer(add_completion=False, no_args_is_help=True)

ROOT_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT_DIR / "templates"
PROJECTS_DIR = ROOT_DIR / "projects"


def create_registry() -> TemplateRegistry:
    registry = TemplateRegistry(TEMPLATES_DIR)
    registry.scan()
    return registry


@app.command()
def list() -> None:
    registry = create_registry()
    templates = registry.list_all()
    if not templates:
        typer.echo("No registered templates.")
        typer.echo(f"Templates dir: {TEMPLATES_DIR}")
        raise typer.Exit()
    typer.echo(f"\nRegistered templates ({len(templates)}):\n")
    typer.echo(f"{'ID':24}{'Name':20}{'Version':10}{'Preset':18}Vars")
    typer.echo("-" * 80)
    for template in templates:
        manifest = template.manifest
        typer.echo(
            f"{manifest.id:24}{manifest.name[:18]:20}{manifest.version:10}"
            f"{(manifest.preset or 'auto'):18}{len(manifest.variables)}"
        )
    typer.echo("")


@app.command()
def info(template_id: str) -> None:
    registry = create_registry()
    info_obj = registry.get(template_id)
    manifest = info_obj.manifest
    typer.echo(f"\nTemplate: {manifest.name}")
    typer.echo(f"ID: {manifest.id}")
    typer.echo(f"Version: {manifest.version}")
    typer.echo(f"Description: {manifest.description}")
    typer.echo(f"Preset: {manifest.preset or 'auto'}")
    if manifest.author:
        typer.echo(f"Author: {manifest.author}")
    if manifest.tags:
        typer.echo(f"Tags: {', '.join(manifest.tags)}")
    typer.echo(f"\nVariables ({len(manifest.variables)}):\n")
    for key, definition in manifest.variables.items():
        required = " [required]" if definition.required else ""
        default = f" (default: {definition.default})" if definition.default is not None else ""
        typer.echo(f"{key}")
        typer.echo(f"  type: {definition.type}{required}")
        typer.echo(f"  label: {definition.label}{default}")
        if definition.options:
            typer.echo(f"  options: {', '.join(definition.options)}")
    typer.echo("")


@app.command()
def init(template_id: str, project_name: str) -> None:
    registry = create_registry()
    info_obj = registry.get(template_id)
    manifest = info_obj.manifest
    project_dir = PROJECTS_DIR / project_name
    materials_dir = project_dir / "materials"
    if project_dir.exists():
        typer.echo(f"Project directory already exists: {project_dir}", err=True)
        raise typer.Exit(code=1)
    materials_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        f"# Project config for {manifest.name}",
        f'template: "{manifest.id}"',
        "",
        f'# preset: "{manifest.preset or "auto"}"',
        '# quality: "high"',
        "",
        "variables:",
    ]
    for key, definition in manifest.variables.items():
        lines.append("")
        lines.append(f"  # {definition.label} [{definition.type}]{' required' if definition.required else ''}")
        if definition.type in {"video", "image", "audio"}:
            suffix = {"video": "mp4", "image": "png", "audio": "mp3"}[definition.type]
            default = (
                str(definition.default)
                if definition.default is not None
                else f"materials/your-file.{suffix}"
            )
            lines.append(f'  {key}: "{default}"')
        elif definition.type == "boolean":
            lines.append(f"  {key}: {json.dumps(bool(definition.default) if definition.default is not None else True)}")
        elif definition.type == "number":
            lines.append(f"  {key}: {definition.default if definition.default is not None else 0}")
        else:
            default = str(definition.default) if definition.default is not None else ""
            lines.append(f'  {key}: "{default}"')
    config_path = project_dir / "config.json"
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    typer.echo(f"\nCreated project: {project_dir}")
    typer.echo(f"  Config: {config_path}")
    typer.echo(f"  Materials: {materials_dir}")
    typer.echo("")


@app.command()
def validate(config: str) -> None:
    registry = create_registry()
    manager = ProjectManager(registry, ROOT_DIR)
    try:
        resolved = manager.resolve(config)
        typer.echo("\nConfig validation passed")
        typer.echo(f"  Template: {resolved.template_info.manifest.name} ({resolved.template_id})")
        typer.echo(f"  Preset: {resolved.preset}")
        typer.echo(f"  Quality: {resolved.quality}")
        typer.echo(f"  Variables: {len(resolved.variables)}")
        typer.echo("")
    except VideoCutError as exc:
        typer.echo(f"\n{exc}\n", err=True)
        raise typer.Exit(code=1)


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
    else:
        typer.echo("  FFmpeg: not found")
    typer.echo(f"  ffprobe: {ffprobe_path or 'not found'}")
    registry = create_registry()
    typer.echo(f"  Templates: {registry.size} ({', '.join(registry.list_ids())})")
    fonts_dir = ROOT_DIR / "fonts"
    if fonts_dir.exists():
        fonts = [path.name for path in fonts_dir.iterdir() if path.suffix.lower() in {'.otf', '.ttf', '.woff', '.woff2'}]
        typer.echo(f"  Fonts: {len(fonts)} ({', '.join(fonts) if fonts else 'none'})")
    typer.echo("")


@app.command()
def render(
    config: str,
    preset: Optional[str] = typer.Option(None),
    quality: Optional[str] = typer.Option(None),
    output: Optional[str] = typer.Option(None),
    preview: bool = typer.Option(False),
) -> None:
    try:
        raw_yaml = load_raw_yaml(Path(config).resolve())
    except VideoCutError as exc:
        typer.echo(f"\n{exc}\n", err=True)
        raise typer.Exit(code=1)

    try:
        if is_pipeline_config(raw_yaml):
            ctx = resolve_pipeline_config(config)
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
        else:
            registry = create_registry()
            resolved = ProjectManager(registry, ROOT_DIR).resolve(config)
            if preset:
                resolved.preset = preset
            if quality:
                resolved.quality = quality
            if preview:
                resolved.preset = "preview"
            if output:
                resolved.output_filename = Path(output).name
            typer.echo("\nStart render")
            typer.echo(f"  Template: {resolved.template_info.manifest.name}")
            typer.echo(f"  Preset: {resolved.preset}")
            typer.echo(f"  Quality: {resolved.quality}")
            result = RenderService(ROOT_DIR).render(
                RenderRequest(
                    template_id=resolved.template_id,
                    template_info=resolved.template_info,
                    variables=resolved.variables,
                    preset=resolved.preset,
                    quality=resolved.quality,
                    output_filename=resolved.output_filename,
                    project_dir=str(resolved.project_dir),
                )
            )
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
        typer.echo(f"\n{exc}\n", err=True)
        raise typer.Exit(code=1)


@app.command("serve")
def serve(host: str = "0.0.0.0", port: int = 3000) -> None:
    uvicorn.run("videocut.api.app:app", host=host, port=port, reload=False)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
