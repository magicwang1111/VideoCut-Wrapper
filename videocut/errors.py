from __future__ import annotations


class VideoCutError(Exception):
    """Base error for the Python runtime."""

    code: int = 1000


class ConfigNotFoundError(VideoCutError):
    code = 1001

    def __init__(self, config_path: str) -> None:
        super().__init__(f"Config file does not exist: {config_path}")


class ConfigParseError(VideoCutError):
    code = 1002

    def __init__(self, config_path: str, detail: str) -> None:
        super().__init__(f"Failed to parse config: {config_path}\n  {detail}")


class ConfigValidationError(VideoCutError):
    code = 1003

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        bullets = "\n".join(f"  - {item}" for item in errors)
        super().__init__(f"Config validation failed with {len(errors)} error(s):\n{bullets}")


class TemplateNotFoundError(VideoCutError):
    code = 1101

    def __init__(self, template_id: str, available: list[str]) -> None:
        available_text = ", ".join(available) if available else "(none)"
        super().__init__(
            f'Template "{template_id}" is not registered.\n'
            f"  Available templates: {available_text}\n"
            "  Run `videocut list` to inspect supported templates."
        )


class ManifestError(VideoCutError):
    code = 1102

    def __init__(self, template_dir: str, detail: str) -> None:
        super().__init__(f"Template manifest error: {template_dir}\n  {detail}")


class AssetNotFoundError(VideoCutError):
    code = 1201

    def __init__(self, asset_path: str, variable_name: str, config_path: str | None = None) -> None:
        location = f"\n  Config: {config_path}" if config_path else ""
        super().__init__(
            "Asset file not found.\n"
            f"  File: {asset_path}\n"
            f"  Variable: {variable_name}{location}\n"
            "  Check the relative path and make sure the file exists."
        )


class RenderError(VideoCutError):
    code = 1301

    def __init__(self, detail: str) -> None:
        super().__init__(f"Render failed.\n  {detail}")


class DependencyError(VideoCutError):
    code = 1401

    def __init__(self, dependency: str, detail: str) -> None:
        super().__init__(f"Missing dependency: {dependency}\n  {detail}")


class PipelineNotFoundError(VideoCutError):
    code = 1501

    def __init__(self, pipeline_name: str, available: list[str]) -> None:
        self.pipeline_name = pipeline_name
        self.available = list(available)
        available_text = ", ".join(available) if available else "(none)"
        super().__init__(
            f'Pipeline "{pipeline_name}" is not registered.\n'
            f"  Available pipelines: {available_text}"
        )


class PipelineDefinitionError(VideoCutError):
    code = 1502

    def __init__(self, config_path: str, detail: str) -> None:
        super().__init__(f"Pipeline definition error: {config_path}\n  {detail}")
