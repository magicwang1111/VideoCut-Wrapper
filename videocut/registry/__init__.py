from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from videocut.errors import ManifestError, TemplateNotFoundError
from videocut.log import get_logger

logger = get_logger(__name__)

VariableType = Literal[
    "video",
    "video_list",
    "image",
    "audio",
    "text",
    "color",
    "number",
    "boolean",
    "select",
]

SUPPORTED_TEMPLATE_IDS = {
    "trim-concat",
    "xfade-concat",
    "trim-xfade-concat",
    "zoom-dissolve-concat",
    "flash-black-concat",
    "trim-mixed-concat",
}


@dataclass(slots=True)
class VariableDefinition:
    type: VariableType
    label: str
    required: bool = False
    default: Any = None
    options: list[str] | None = None
    min: float | None = None
    max: float | None = None


@dataclass(slots=True)
class TemplateManifest:
    id: str
    name: str
    description: str
    version: str
    entry: str
    variables: dict[str, VariableDefinition]
    author: str | None = None
    preset: str | None = None
    tags: list[str] | None = None


@dataclass(slots=True)
class TemplateInfo:
    manifest: TemplateManifest
    directory: Path
    entry_path: Path
    assets_dir: Path


REQUIRED_MANIFEST_FIELDS = ("id", "name", "description", "version", "entry", "variables")


class TemplateRegistry:
    def __init__(self, templates_root: str | Path) -> None:
        self.templates_root = Path(templates_root).resolve()
        self._templates: dict[str, TemplateInfo] = {}

    def scan(self) -> None:
        self._templates.clear()
        if not self.templates_root.exists():
            return
        for entry in sorted(self.templates_root.iterdir()):
            if not entry.is_dir():
                continue
            manifest_path = entry / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                info = self._register_from_manifest(entry, manifest_path)
            except (OSError, ManifestError) as exc:
                logger.warning('skipping template "%s": %s', entry.name, exc)
                continue
            if info.manifest.id not in SUPPORTED_TEMPLATE_IDS:
                continue
            self._templates[info.manifest.id] = info

    def _register_from_manifest(self, template_dir: Path, manifest_path: Path) -> TemplateInfo:
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ManifestError(str(template_dir), f"manifest.json is not valid JSON: {exc}") from exc

        for field in REQUIRED_MANIFEST_FIELDS:
            if field not in raw:
                raise ManifestError(str(template_dir), f'missing required field "{field}"')

        variables_raw = raw.get("variables")
        if not isinstance(variables_raw, dict):
            raise ManifestError(str(template_dir), '"variables" must be an object')

        variables: dict[str, VariableDefinition] = {}
        for key, value in variables_raw.items():
            if not isinstance(value, dict):
                raise ManifestError(str(template_dir), f'variable "{key}" must be an object')
            if "type" not in value:
                raise ManifestError(str(template_dir), f'variable "{key}" missing "type"')
            if "label" not in value:
                raise ManifestError(str(template_dir), f'variable "{key}" missing "label"')
            variables[key] = VariableDefinition(
                type=value["type"],
                label=value["label"],
                required=bool(value.get("required", False)),
                default=value.get("default"),
                options=list(value["options"]) if isinstance(value.get("options"), list) else None,
                min=value.get("min"),
                max=value.get("max"),
            )

        # The Python runtime keeps the legacy "entry" field for manifest compatibility,
        # but no longer executes template source files directly.
        entry_path = (template_dir / str(raw["entry"])).resolve()

        manifest = TemplateManifest(
            id=str(raw["id"]),
            name=str(raw["name"]),
            description=str(raw["description"]),
            version=str(raw["version"]),
            entry=str(raw["entry"]),
            variables=variables,
            author=str(raw["author"]) if raw.get("author") is not None else None,
            preset=str(raw["preset"]) if raw.get("preset") is not None else None,
            tags=[str(item) for item in raw.get("tags", [])] if isinstance(raw.get("tags"), list) else None,
        )
        return TemplateInfo(
            manifest=manifest,
            directory=template_dir.resolve(),
            entry_path=entry_path,
            assets_dir=(template_dir / "assets").resolve(),
        )

    def get(self, template_id: str) -> TemplateInfo:
        info = self._templates.get(template_id)
        if info is None:
            raise TemplateNotFoundError(template_id, self.list_ids())
        return info

    def has(self, template_id: str) -> bool:
        return template_id in self._templates

    def list_ids(self) -> list[str]:
        return list(self._templates.keys())

    def list_all(self) -> list[TemplateInfo]:
        return list(self._templates.values())

    @property
    def size(self) -> int:
        return len(self._templates)
