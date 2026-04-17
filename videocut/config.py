from __future__ import annotations

from pathlib import Path

from videocut.pipeline import PipelineRegistry


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


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


REGISTERED_PIPELINES = scan_registered_pipelines()

REGISTERED_PIPELINE_NAMES = list(REGISTERED_PIPELINES.keys())


__all__ = [
    "REGISTERED_PIPELINES",
    "REGISTERED_PIPELINE_NAMES",
    "scan_registered_pipelines",
]
