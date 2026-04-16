from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from videocut.errors import ConfigValidationError
from videocut.project.asset_resolver import resolve_assets
from videocut.project.config_parser import ProjectConfig, get_project_dir, parse_project_config
from videocut.registry import TemplateInfo, TemplateRegistry
from videocut.registry.schema_validator import validate_variables


@dataclass(slots=True)
class ResolvedProject:
    template_id: str
    template_info: TemplateInfo
    variables: dict[str, Any]
    preset: str
    quality: str
    output_filename: str | None
    project_dir: Path
    config_path: Path


class ProjectManager:
    def __init__(self, registry: TemplateRegistry, root_dir: str | Path) -> None:
        self.registry = registry
        self.root_dir = Path(root_dir).resolve()

    def resolve(self, config_path: str | Path) -> ResolvedProject:
        abs_config_path = Path(config_path).resolve()
        project_dir = get_project_dir(abs_config_path)
        config: ProjectConfig = parse_project_config(abs_config_path)
        template_info = self.registry.get(config.template)
        manifest = template_info.manifest

        validation_result = validate_variables(manifest.variables, config.variables)
        if not validation_result.valid:
            raise ConfigValidationError(validation_result.errors)

        resolved_vars = resolve_assets(
            validation_result.merged,
            manifest.variables,
            project_dir,
            template_info,
            self.root_dir,
            str(abs_config_path),
        )

        return ResolvedProject(
            template_id=config.template,
            template_info=template_info,
            variables=resolved_vars,
            preset=config.preset or manifest.preset or "auto",
            quality=config.quality or "high",
            output_filename=config.output.filename if config.output else None,
            project_dir=project_dir,
            config_path=abs_config_path,
        )

