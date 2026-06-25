from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import quote, urlparse
from uuid import uuid4

from videocut.errors import RenderError

_BGM_EXTENSIONS = {".mp3", ".wav", ".aac", ".ogg", ".flac", ".m4a"}
_DEFAULT_BGM_OSS_URI = "oss://goumee-coze/GouMei-Video-Cut/bgm/"
_DEFAULT_BGM_TEMPLATE_OSS_URI = "oss://goumee-coze/GouMei-Video-Cut/bgm-templete/"
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


class BgmTemplateSyncError(RuntimeError):
    def __init__(self, reason: str, detail: str, *, returncode: int | None = None) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.returncode = returncode


def allowed_bgm_extensions() -> list[str]:
    return sorted(_BGM_EXTENSIONS)


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


def resolve_bgm_template_dir(root_dir: str | Path) -> Path:
    env_dir = os.getenv("BGM_TEMPLATE_DIR")
    raw_dir = env_dir.strip() if env_dir and env_dir.strip() else "input/bgm-templete"
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


def resolve_bgm_template_oss_uri(configured_uri: str | None = None) -> str:
    env_uri = os.getenv("BGM_TEMPLATE_OSS_URI")
    raw_uri = (
        env_uri.strip()
        if env_uri and env_uri.strip()
        else (configured_uri.strip() if configured_uri and configured_uri.strip() else _DEFAULT_BGM_TEMPLATE_OSS_URI)
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


def normalize_bgm_filename_stem(configured_filename: str) -> str:
    return _normalize_bgm_filename_stem(configured_filename)


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


def normalize_bgm_category(configured_category: str) -> str:
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
    return "/".join(category_parts)


def _resolve_bgm_category_dir(bgm_dir: Path, configured_category: str, *, require_exists: bool) -> Path:
    raw_category = normalize_bgm_category(configured_category)
    relative_category = Path(raw_category)

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


def resolve_bgm_category_dir_for_write(bgm_dir: Path, configured_category: str) -> Path:
    return _resolve_bgm_category_dir(bgm_dir, configured_category, require_exists=False)


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


def validate_bgm_directory_files(bgm_dir: Path) -> dict[str, object]:
    base_dir = bgm_dir.resolve()
    invalid_files: list[dict[str, str]] = []
    valid_audio_files = 0
    if base_dir.is_dir():
        for item in sorted((p for p in base_dir.rglob("*") if p.is_file()), key=lambda p: p.relative_to(base_dir).as_posix()):
            relative_path = item.relative_to(base_dir).as_posix()
            suffix = item.suffix.lower()
            if suffix in _BGM_EXTENSIONS:
                valid_audio_files += 1
            else:
                invalid_files.append(
                    {
                        "path": relative_path,
                        "reason": "unsupported_extension",
                        "extension": suffix,
                    }
                )
    return {
        "validAudioFiles": valid_audio_files,
        "invalidFiles": invalid_files,
        "allowedExtensions": allowed_bgm_extensions(),
    }


def _truncate_sync_detail(value: str) -> str:
    detail = value.strip()
    if len(detail) > 2000:
        return "...\n" + detail[-2000:]
    return detail


def _resolve_ossutil_path() -> str:
    raw_path = os.getenv("OSSUTIL_PATH", "ossutil64").strip() or "ossutil64"
    resolved = shutil.which(raw_path)
    if resolved:
        return resolved
    candidate = Path(raw_path)
    if candidate.is_file():
        return str(candidate)
    raise BgmTemplateSyncError("ossutil_not_found", f"OSSUTIL_PATH is not executable or not found: {raw_path}")


def sync_bgm_template_from_oss(root_dir: str | Path, *, category: str | None = None) -> dict[str, object]:
    template_dir = resolve_bgm_template_dir(root_dir)
    template_oss_uri = resolve_bgm_template_oss_uri()
    normalized_category = normalize_bgm_category(category) if isinstance(category, str) and category.strip() else None
    scope = "category" if normalized_category else "all"
    source_uri = template_oss_uri if normalized_category is None else template_oss_uri + normalized_category.rstrip("/") + "/"
    target_dir = (
        template_dir
        if normalized_category is None
        else resolve_bgm_category_dir_for_write(template_dir, normalized_category)
    )
    target_dir.mkdir(parents=True, exist_ok=True)

    endpoint = os.getenv("OSS_ENDPOINT")
    access_key_id = os.getenv("OSS_ACCESS_KEY_ID")
    access_key_secret = os.getenv("OSS_ACCESS_KEY_SECRET")
    if not endpoint or not access_key_id or not access_key_secret:
        raise BgmTemplateSyncError(
            "missing_credentials",
            "OSS_ENDPOINT / OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET are required for BGM template sync.",
        )

    command = [
        _resolve_ossutil_path(),
        "sync",
        source_uri,
        str(target_dir) + os.sep,
        "-e",
        endpoint,
        "-i",
        access_key_id,
        "-k",
        access_key_secret,
        "-u",
        "-f",
    ]
    sts_token = os.getenv("OSS_STS_TOKEN")
    if sts_token:
        command.extend(["-t", sts_token])

    timeout_seconds = int(os.getenv("BGM_TEMPLATE_SYNC_TIMEOUT_SECONDS", "600"))
    started_at = time.monotonic()
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise BgmTemplateSyncError(
            "timeout",
            f"ossutil sync timed out after {timeout_seconds} seconds.",
        ) from exc
    if result.returncode != 0:
        detail = _truncate_sync_detail(result.stderr or result.stdout or "")
        raise BgmTemplateSyncError("ossutil_failed", detail, returncode=result.returncode)

    validation_root = target_dir if normalized_category else template_dir
    return {
        "scope": scope,
        "category": normalized_category,
        "templateBgmRoot": str(template_dir.resolve()),
        "templateBgmOssUri": source_uri,
        "durationSeconds": round(time.monotonic() - started_at, 3),
        "validation": validate_bgm_directory_files(validation_root),
    }


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
