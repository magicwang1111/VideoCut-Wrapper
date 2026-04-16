from __future__ import annotations

import shutil
from pathlib import Path

import oss2

from videocut.errors import DependencyError


class OssClient:
    def __init__(self) -> None:
        import os

        self.bucket_name = os.getenv("OSS_BUCKET", "goumee-coze")
        self.prefix = os.getenv("OSS_PREFIX", "GouMei-Video-Cut")
        endpoint = os.getenv("OSS_ENDPOINT", "oss-cn-hangzhou-internal.aliyuncs.com")
        access_key_id = os.getenv("OSS_ACCESS_KEY_ID")
        access_key_secret = os.getenv("OSS_ACCESS_KEY_SECRET")
        self.local_root = os.getenv("OSS_LOCAL_ROOT")
        if self.local_root:
            self.local_root = str(Path(self.local_root).resolve())
            self.bucket = None
            self.endpoint = endpoint
            return
        if not access_key_id or not access_key_secret:
            raise DependencyError(
                "OSS credentials",
                "Set OSS_ACCESS_KEY_ID and OSS_ACCESS_KEY_SECRET, or configure OSS_LOCAL_ROOT for local testing.",
            )
        auth = oss2.Auth(access_key_id, access_key_secret)
        self.bucket = oss2.Bucket(auth, endpoint, self.bucket_name)
        self.endpoint = endpoint

    def input_key(self, file_id: str, ext: str) -> str:
        return f"{self.prefix}/inputs/{file_id}{ext}"

    def output_key(self, task_id: str) -> str:
        return f"{self.prefix}/outputs/{task_id}/final.mp4"

    def upload(self, local_path: str | Path, oss_key: str) -> None:
        if self.local_root:
            target = Path(self.local_root) / oss_key
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_path, target)
            return
        if self.bucket is None:
            raise DependencyError("OSS bucket", "Bucket not initialized; set OSS credentials or OSS_LOCAL_ROOT.")
        self.bucket.put_object_from_file(oss_key, str(local_path))

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

    def presign_url(self, oss_key: str, expires_seconds: int = 3600) -> str:
        if self.local_root:
            return str((Path(self.local_root) / oss_key).resolve())
        if self.bucket is None:
            raise DependencyError("OSS bucket", "Bucket not initialized; set OSS credentials or OSS_LOCAL_ROOT.")
        return self.bucket.sign_url("GET", oss_key, expires_seconds)

