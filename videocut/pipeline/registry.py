from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from videocut.errors import PipelineDefinitionError, PipelineNotFoundError, VideoCutError
from videocut.pipeline.config import load_raw_yaml, parse_pipeline_config
from videocut.pipeline.types import PipelineConfig


@dataclass(slots=True)
class RegisteredPipeline:
    name: str
    source_path: Path
    config: PipelineConfig


class PipelineRegistry:
    def __init__(self, pipelines_root: str | Path) -> None:
        self.pipelines_root = Path(pipelines_root).resolve()
        self._pipelines: dict[str, RegisteredPipeline] = {}

    def scan(self) -> None:
        self._pipelines.clear()
        if not self.pipelines_root.exists():
            return
        for config_path in sorted(self._iter_config_paths()):
            raw = load_raw_yaml(config_path)
            try:
                config = parse_pipeline_config(raw, config_path, require_name=True)
            except VideoCutError as exc:
                raise PipelineDefinitionError(str(config_path), str(exc)) from exc
            pipeline_name = config.name or ""
            if pipeline_name != config_path.parent.name:
                raise PipelineDefinitionError(
                    str(config_path),
                    f'name "{pipeline_name}" must match directory "{config_path.parent.name}"',
                )
            if pipeline_name in self._pipelines:
                previous = self._pipelines[pipeline_name].source_path
                raise PipelineDefinitionError(
                    str(config_path),
                    f'duplicate pipeline name "{pipeline_name}" already defined in {previous}',
                )
            self._pipelines[pipeline_name] = RegisteredPipeline(
                name=pipeline_name,
                source_path=config_path.resolve(),
                config=config,
            )

    def _iter_config_paths(self) -> list[Path]:
        config_paths: list[Path] = []
        for entry in self.pipelines_root.iterdir():
            if not entry.is_dir():
                continue
            matches = [entry / "config.json", entry / "config.yaml", entry / "config.yml"]
            config_path = next((path for path in matches if path.exists()), None)
            if config_path is not None:
                config_paths.append(config_path)
        return config_paths

    def get(self, pipeline_name: str) -> RegisteredPipeline:
        info = self._pipelines.get(pipeline_name)
        if info is None:
            raise PipelineNotFoundError(pipeline_name, self.list_names())
        return info

    def list_names(self) -> list[str]:
        return list(self._pipelines.keys())

    def list_all(self) -> list[RegisteredPipeline]:
        return list(self._pipelines.values())

    @property
    def size(self) -> int:
        return len(self._pipelines)


def serialize_registered_pipeline(item: RegisteredPipeline) -> dict[str, object]:
    return {
        "name": item.name,
        "source_path": str(item.source_path),
        "config": asdict(item.config),
    }
