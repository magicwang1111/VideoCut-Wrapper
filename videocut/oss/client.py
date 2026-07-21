from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import oss2

from videocut.errors import DependencyError
from videocut.runtime_paths import project_root, resolve_runtime_path
from videocut.subtitle.config import subtitle_input_subdir, subtitle_output_subdir


try:
    BEIJING_TZ = ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:
    BEIJING_TZ = timezone(timedelta(hours=8))

_DEFAULT_PUBLIC_ENDPOINT = "oss-cn-hangzhou.aliyuncs.com"


def _as_output_time(timestamp: datetime | None = None) -> datetime:
    if timestamp is None:
        return datetime.now(BEIJING_TZ)
    if timestamp.tzinfo is None:
        return timestamp
    return timestamp.astimezone(BEIJING_TZ)


def _normalize_public_endpoint(raw_endpoint: str | None) -> str:
    endpoint = (raw_endpoint or _DEFAULT_PUBLIC_ENDPOINT).strip()
    endpoint = endpoint.removeprefix("https://").removeprefix("http://").strip("/")
    return endpoint or _DEFAULT_PUBLIC_ENDPOINT


class OssClient:
    def __init__(self) -> None:
        self.bucket_name = os.getenv("OSS_BUCKET", "goumee-coze")
        self.prefix = (os.getenv("OSS_PREFIX", "GouMei-Video-Cut").strip().strip("/") or "GouMei-Video-Cut")
        endpoint = os.getenv("OSS_ENDPOINT", "oss-cn-hangzhou.aliyuncs.com")
        access_key_id = os.getenv("OSS_ACCESS_KEY_ID")
        access_key_secret = os.getenv("OSS_ACCESS_KEY_SECRET")
        sts_token = os.getenv("OSS_STS_TOKEN")
        self.upload_backend = (os.getenv("OSS_UPLOAD_BACKEND", "ossutil").strip().lower() or "ossutil")
        self.ossutil_path = os.getenv("OSSUTIL_PATH", "ossutil64").strip() or "ossutil64"
        self.ossutil_timeout_seconds = int(os.getenv("OSSUTIL_TIMEOUT_SECONDS", "600"))
        self.public_endpoint = _normalize_public_endpoint(os.getenv("OSS_PUBLIC_ENDPOINT"))
        self.local_root = os.getenv("OSS_LOCAL_ROOT")
        if self.local_root:
            self.local_root = str(resolve_runtime_path(self.local_root, project_root() / "oss-local"))
            self.bucket = None
            self.endpoint = endpoint
            self.access_key_id = access_key_id
            self.access_key_secret = access_key_secret
            self.sts_token = sts_token
            return
        if not access_key_id or not access_key_secret:
            raise DependencyError(
                "OSS credentials",
                "Set OSS_ACCESS_KEY_ID and OSS_ACCESS_KEY_SECRET, or configure OSS_LOCAL_ROOT for local testing.",
            )
        auth = oss2.Auth(access_key_id, access_key_secret)
        self.bucket = oss2.Bucket(auth, endpoint, self.bucket_name)
        self.endpoint = endpoint
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.sts_token = sts_token

    def input_key(self, file_id: str, ext: str) -> str:
        return f"{self.prefix}/inputs/{file_id}{ext}"

    @property
    def subtitle_input_prefix(self) -> str:
        return f"{self.prefix}/{subtitle_input_subdir()}/"

    def subtitle_input_key(self, file_id: str, ext: str) -> str:
        return f"{self.subtitle_input_prefix}{file_id}{ext}"

    def user_audio_key(self, file_id: str, ext: str) -> str:
        return f"{self.prefix}/user-audio/{file_id}{ext}"

    def output_key(self, task_id: str, timestamp: datetime | None = None) -> str:
        output_time = _as_output_time(timestamp)
        date_dir = output_time.strftime("%Y%m%d")
        timestamp_dir = output_time.strftime("%Y%m%d_%H%M%S")
        return f"{self.prefix}/outputs/{date_dir}/{timestamp_dir}/{task_id}/final.mp4"

    def subtitle_output_key(self, task_id: str, timestamp: datetime | None = None) -> str:
        output_time = _as_output_time(timestamp)
        date_dir = output_time.strftime("%Y%m%d")
        timestamp_dir = output_time.strftime("%Y%m%d_%H%M%S")
        return f"{self.prefix}/{subtitle_output_subdir()}/{date_dir}/{timestamp_dir}/{task_id}/final.mp4"

    def exists(self, oss_key: str) -> bool:
        if self.local_root:
            return (Path(self.local_root) / oss_key).is_file()
        if self.bucket is None:
            return False
        return bool(self.bucket.object_exists(oss_key))

    def signed_get_url(self, oss_key: str, expires: int = 86400) -> str:
        if self.local_root:
            raise DependencyError("OSS signed URL", "A public OSS backend is required for Tencent MPS URL input.")
        if self.bucket is None:
            raise DependencyError("OSS bucket", "Bucket not initialized.")
        return self.bucket.sign_url("GET", oss_key, expires, slash_safe=True)

    def upload(self, local_path: str | Path, oss_key: str) -> None:
        if self.local_root:
            target = Path(self.local_root) / oss_key
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_path, target)
            return
        if self.bucket is None:
            raise DependencyError("OSS bucket", "Bucket not initialized; set OSS credentials or OSS_LOCAL_ROOT.")
        if self.upload_backend == "ossutil":
            self._upload_with_ossutil(local_path, oss_key)
            return
        if self.upload_backend != "oss2":
            raise DependencyError("OSS upload backend", f"Unsupported OSS_UPLOAD_BACKEND: {self.upload_backend}")
        self.bucket.put_object_from_file(oss_key, str(local_path))

    def _resolve_ossutil_path(self) -> str:
        resolved = shutil.which(self.ossutil_path)
        if resolved:
            return resolved
        candidate = Path(self.ossutil_path)
        if candidate.is_file():
            return str(candidate)
        raise DependencyError("ossutil", f"OSSUTIL_PATH is not executable or not found: {self.ossutil_path}")

    def _upload_with_ossutil(self, local_path: str | Path, oss_key: str) -> None:
        if not self.access_key_id or not self.access_key_secret:
            raise DependencyError("OSS credentials", "ossutil upload requires OSS_ACCESS_KEY_ID and OSS_ACCESS_KEY_SECRET.")
        command = [
            self._resolve_ossutil_path(),
            "cp",
            str(local_path),
            f"oss://{self.bucket_name}/{oss_key}",
            "-e",
            self.endpoint,
            "-i",
            self.access_key_id,
            "-k",
            self.access_key_secret,
            "-f",
        ]
        if self.sts_token:
            command.extend(["-t", self.sts_token])
        try:
            result = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.ossutil_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"ossutil upload timed out after {self.ossutil_timeout_seconds} seconds.") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            if len(detail) > 2000:
                detail = "...\n" + detail[-2000:]
            raise RuntimeError(f"ossutil upload failed with code {result.returncode}: {detail}")

    def download(self, oss_key: str, local_path: str | Path) -> None:
        target_path = Path(local_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if self.local_root:
            source = Path(self.local_root) / oss_key
            shutil.copy2(source, target_path)
            return
        if self.bucket is None:
            raise DependencyError("OSS bucket", "Bucket not initialized; set OSS credentials or OSS_LOCAL_ROOT.")
        self.bucket.get_object_to_file(oss_key, str(target_path))

    def delete(self, oss_key: str) -> None:
        if self.local_root:
            (Path(self.local_root) / oss_key).unlink(missing_ok=True)
            return
        if self.bucket is None:
            raise DependencyError("OSS bucket", "Bucket not initialized; set OSS credentials or OSS_LOCAL_ROOT.")
        self.bucket.delete_object(oss_key)

    def public_url(self, oss_key: str) -> str:
        if self.local_root:
            return str((Path(self.local_root) / oss_key).resolve())
        encoded_key = quote(oss_key, safe="/")
        return f"https://{self.bucket_name}.{self.public_endpoint}/{encoded_key}"
