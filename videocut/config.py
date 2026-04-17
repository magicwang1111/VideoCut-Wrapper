from __future__ import annotations

from pathlib import Path

from videocut.pipeline import PipelineRegistry
from videocut.registry import TemplateRegistry


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def scan_registered_templates() -> dict[str, dict[str, str]]:
    registry = TemplateRegistry(_repo_root() / "templates")
    registry.scan()

    items: dict[str, dict[str, str]] = {}
    for info in sorted(registry.list_all(), key=lambda item: item.manifest.id):
        items[info.manifest.id] = {
            "id": info.manifest.id,
            "name": info.manifest.name,
            "description": info.manifest.description,
            "manifest_path": str((info.directory / "manifest.json").resolve()),
        }
    return items


def scan_registered_pipelines() -> dict[str, dict[str, str]]:
    registry = PipelineRegistry(_repo_root() / "pipelines")
    registry.scan()

    items: dict[str, dict[str, str]] = {}
    for info in sorted(registry.list_all(), key=lambda item: item.name):
        items[info.name] = {
            "name": info.name,
            "source_path": str(info.source_path.resolve()),
        }
    return items


REGISTERED_TEMPLATES = scan_registered_templates()
REGISTERED_PIPELINES = scan_registered_pipelines()

REGISTERED_TEMPLATE_IDS = list(REGISTERED_TEMPLATES.keys())
REGISTERED_PIPELINE_NAMES = list(REGISTERED_PIPELINES.keys())


__all__ = [
    "REGISTERED_PIPELINES",
    "REGISTERED_PIPELINE_NAMES",
    "REGISTERED_TEMPLATES",
    "REGISTERED_TEMPLATE_IDS",
    "scan_registered_pipelines",
    "scan_registered_templates",
]
