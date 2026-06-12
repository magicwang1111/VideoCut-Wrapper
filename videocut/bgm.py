from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import quote, urlparse
from uuid import uuid4

from videocut.errors import RenderError

_BGM_EXTENSIONS = {".mp3", ".wav", ".aac", ".ogg", ".flac", ".m4a"}
_DEFAULT_BGM_OSS_URI = "oss://goumee-coze/GouMei-Video-Cut/bgm/"
_DEFAULT_OSS_BUCKET = "goumee-coze"
_DEFAULT_OSS_ENDPOINT = "oss-cn-hangzhou.aliyuncs.com"
_BGM_CATEGORY_DISPLAY_NAMES = {
    "calm": "舒缓",
    "intense": "激烈",
}
_BGM_MANIFEST_PATH_RULE = (
    "API overrides.bgm.category + overrides.bgm.filename uses the category and extensionless filename fields below, "
    "relative to /app/input/bgm."
)


def resolve_bgm_dir(root_dir: str | Path, configured_dir: str | None = None) -> Path:
    env_dir = os.getenv("BGM_DIR")
    raw_dir = env_dir.strip() if env_dir and env_dir.strip() else (configured_dir.strip() if configured_dir and configured_dir.strip() else None)
    if not raw_dir:
        return Path(root_dir).resolve() / "input" / "bgm"
    path_obj = Path(raw_dir).expanduser()
    if path_obj.is_absolute():
        return path_obj.resolve()
    return (Path(root_dir).resolve() / path_obj).resolve()


def resolve_bgm_backup_dir(root_dir: str | Path) -> Path:
    env_dir = os.getenv("BGM_BACKUP_DIR")
    raw_dir = env_dir.strip() if env_dir and env_dir.strip() else "input/bgm-backup"
    path_obj = Path(raw_dir).expanduser()
    if path_obj.is_absolute():
        return path_obj.resolve()
    return (Path(root_dir).resolve() / path_obj).resolve()


def scan_bgm_files(bgm_dir: Path) -> list[Path]:
    if not bgm_dir.is_dir():
        raise RenderError(f"BGM directory does not exist: {bgm_dir}")
    files = sorted(
        p for p in bgm_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in _BGM_EXTENSIONS
    )
    if not files:
        raise RenderError(f"No audio files found in BGM directory or subdirectories: {bgm_dir}")
    return files


def resolve_bgm_oss_uri(configured_uri: str | None = None) -> str:
    env_uri = os.getenv("BGM_OSS_URI")
    raw_uri = (
        env_uri.strip()
        if env_uri and env_uri.strip()
        else (configured_uri.strip() if configured_uri and configured_uri.strip() else _DEFAULT_BGM_OSS_URI)
    )
    return raw_uri.rstrip("/") + "/"


def build_bgm_oss_url(base_uri: str, category: str, filename: str) -> str:
    parsed = urlparse(base_uri)
    bucket = parsed.netloc or os.getenv("OSS_BUCKET", _DEFAULT_OSS_BUCKET)
    object_prefix = parsed.path.strip("/")
    relative_path = (PurePosixPath(category) / filename).as_posix() if category else filename
    object_key = (PurePosixPath(object_prefix) / relative_path).as_posix() if object_prefix else relative_path
    encoded_key = quote(object_key, safe="/")
    endpoint = os.getenv("OSS_PUBLIC_ENDPOINT") or _DEFAULT_OSS_ENDPOINT
    public_host = endpoint.removeprefix("https://").removeprefix("http://").strip("/")
    return f"https://{bucket}.{public_host}/{encoded_key}"


def display_bgm_category(category: str) -> str:
    if not category:
        return ""
    return "/".join(_BGM_CATEGORY_DISPLAY_NAMES.get(part, part) for part in category.split("/"))


def _normalize_bgm_filename_stem(configured_filename: str) -> str:
    raw_filename = configured_filename.strip().replace("\\", "/")
    if not raw_filename:
        raise RenderError("BGM filename must not be empty.")

    relative_filename = Path(raw_filename)
    posix_filename = PurePosixPath(raw_filename)
    windows_filename = PureWindowsPath(raw_filename)
    filename_parts = [part for part in raw_filename.split("/") if part]
    if (
        len(filename_parts) != 1
        or relative_filename.is_absolute()
        or posix_filename.is_absolute()
        or windows_filename.is_absolute()
        or windows_filename.drive
        or "." in raw_filename
        or any(part in {".", ".."} for part in filename_parts)
    ):
        raise RenderError(f"BGM filename must be an extensionless plain file name under BGM category: {configured_filename}")
    return raw_filename


def list_bgm_catalog(bgm_dir: Path, *, oss_uri_base: str | None = None) -> dict[str, object]:
    base_dir = bgm_dir.resolve()
    if not base_dir.is_dir():
        raise RenderError(f"BGM directory does not exist: {base_dir}")

    resolved_oss_uri = resolve_bgm_oss_uri(oss_uri_base)
    files: list[dict[str, str]] = []
    category_counts: dict[str, int] = {}
    audio_files = sorted(
        (
            p
            for p in base_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in _BGM_EXTENSIONS
        ),
        key=lambda p: p.relative_to(base_dir).as_posix(),
    )
    seen_stems: dict[tuple[str, str], Path] = {}
    for audio_file in audio_files:
        relative_parent = audio_file.parent.relative_to(base_dir).as_posix()
        category = "" if relative_parent == "." else relative_parent
        filename_stem = _normalize_bgm_filename_stem(audio_file.stem)
        stem_key = (category, filename_stem)
        previous = seen_stems.get(stem_key)
        if previous is not None:
            raise RenderError(
                f"Duplicate BGM filename stem in category {category or '<root>'}: "
                f"{filename_stem} ({previous.name}, {audio_file.name})"
            )
        seen_stems[stem_key] = audio_file
        files.append(
            {
                "category": category,
                "displayName": display_bgm_category(category),
                "filename": filename_stem,
                "ossUrl": build_bgm_oss_url(resolved_oss_uri, category, audio_file.name),
            }
        )
        category_counts[category] = category_counts.get(category, 0) + 1

    categories = [
        {"name": category, "displayName": display_bgm_category(category), "count": category_counts[category]}
        for category in sorted(category_counts)
    ]
    return {
        "bgmRoot": str(base_dir),
        "categories": categories,
        "files": files,
    }


def build_bgm_manifest(bgm_dir: Path, *, api_bgm_root: str = "/app/input/bgm") -> dict[str, object]:
    base_dir = bgm_dir.resolve()
    catalog = list_bgm_catalog(base_dir)
    return {
        "bgmRoot": api_bgm_root,
        "pathRule": _BGM_MANIFEST_PATH_RULE,
        "generatedFrom": str(base_dir),
        "categories": catalog["categories"],
        "files": catalog["files"],
    }


def write_bgm_manifest(
    bgm_dir: Path,
    output_path: Path,
    *,
    api_bgm_root: str = "/app/input/bgm",
) -> dict[str, object]:
    manifest = build_bgm_manifest(bgm_dir, api_bgm_root=api_bgm_root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def _resolve_bgm_category_dir(bgm_dir: Path, configured_category: str, *, require_exists: bool) -> Path:
    raw_category = configured_category.strip().replace("\\", "/")
    if not raw_category:
        raise RenderError("BGM category must not be empty.")

    relative_category = Path(raw_category)
    posix_category = PurePosixPath(raw_category)
    windows_category = PureWindowsPath(raw_category)
    category_parts = [part for part in raw_category.split("/") if part]
    if (
        relative_category.is_absolute()
        or posix_category.is_absolute()
        or windows_category.is_absolute()
        or windows_category.drive
        or any(part in {".", ".."} for part in category_parts)
    ):
        raise RenderError(f"BGM category must be a relative directory under BGM directory: {configured_category}")

    base_dir = bgm_dir.resolve()
    category_dir = (base_dir / relative_category).resolve()
    try:
        category_dir.relative_to(base_dir)
    except ValueError as exc:
        raise RenderError(f"BGM category must stay under BGM directory: {configured_category}") from exc
    if require_exists and not category_dir.is_dir():
        raise RenderError(f"BGM category directory not found: {category_dir}")
    return category_dir


def resolve_bgm_category_dir(bgm_dir: Path, configured_category: str) -> Path:
    return _resolve_bgm_category_dir(bgm_dir, configured_category, require_exists=True)


def resolve_bgm_category_dir_optional(bgm_dir: Path, configured_category: str) -> Path | None:
    category_dir = _resolve_bgm_category_dir(bgm_dir, configured_category, require_exists=False)
    return category_dir if category_dir.is_dir() else None


def scan_bgm_category_files(bgm_dir: Path, configured_category: str) -> list[Path]:
    return scan_bgm_files(resolve_bgm_category_dir(bgm_dir, configured_category))


def _find_bgm_category_file(
    category_dir: Path,
    configured_category: str,
    configured_filename: str,
    raw_filename: str,
) -> Path | None:
    if not category_dir.is_dir():
        return None
    candidates = sorted(
        p
        for p in category_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _BGM_EXTENSIONS and p.stem == raw_filename
    )
    if len(candidates) > 1:
        names = ", ".join(p.name for p in candidates)
        raise RenderError(f"Duplicate BGM filename stem in category {configured_category}: {configured_filename} ({names})")
    if not candidates:
        return None
    return candidates[0].resolve()


def resolve_bgm_category_file(
    bgm_dir: Path,
    configured_category: str,
    configured_filename: str,
    *,
    backup_bgm_dir: Path | None = None,
) -> Path:
    category_dir = _resolve_bgm_category_dir(bgm_dir, configured_category, require_exists=False)
    raw_filename = _normalize_bgm_filename_stem(configured_filename)

    primary = _find_bgm_category_file(category_dir, configured_category, configured_filename, raw_filename)
    if primary is not None:
        return primary

    backup_category_dir = None
    if backup_bgm_dir is not None:
        backup_category_dir = _resolve_bgm_category_dir(backup_bgm_dir, configured_category, require_exists=False)
        backup = _find_bgm_category_file(backup_category_dir, configured_category, configured_filename, raw_filename)
        if backup is not None:
            return backup

    message = f"BGM file not found for filename stem: {category_dir / raw_filename}"
    if backup_category_dir is not None:
        message += f" (backup checked: {backup_category_dir / raw_filename})"
    raise RenderError(message)


def apply_bgm(
    ffmpeg_path: str,
    ffprobe_path: str,
    video_path: str,
    bgm_file: Path,
    volume: float,
    fade_out: float,
    task_id: str | None = None,
) -> None:
    video_file = Path(video_path)
    tmp_token = task_id or "bgm"
    tmp_path = video_file.with_name(f"{video_file.name}.{tmp_token}.{uuid4().hex}.bgm_tmp.mp4")
    raw = subprocess.check_output(
        [ffprobe_path, "-v", "error", "-print_format", "json", "-show_format", video_path],
        encoding="utf-8",
        timeout=10,
    )
    video_duration = float(json.loads(raw)["format"]["duration"])

    audio_filter = (
        f"[1:a]aloop=loop=-1:size=2000000000,"
        f"volume={volume:.4f},"
        f"atrim=end={video_duration:.6f}"
    )
    if fade_out > 0:
        fade_start = max(0.0, video_duration - fade_out)
        audio_filter += f",afade=t=out:st={fade_start:.6f}:d={fade_out:.4f}"
    audio_filter += "[bgm]"

    try:
        subprocess.run(
            [
                ffmpeg_path,
                "-i",
                video_path,
                "-i",
                str(bgm_file),
                "-filter_complex",
                audio_filter,
                "-map",
                "0:v",
                "-map",
                "[bgm]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-y",
                str(tmp_path),
            ],
            check=True,
            timeout=600,
        )
        tmp_path.replace(video_file)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
