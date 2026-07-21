from __future__ import annotations

import os
from dataclasses import dataclass


def _positive_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}.")
    return value


def _positive_float(name: str, default: float, minimum: float = 0.1) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number.") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}.")
    return value


def _safe_subdir(name: str, default: str) -> str:
    value = (os.getenv(name) or default).strip().strip("/")
    if not value or value in {".", ".."} or "/" in value or "\\" in value or ".." in value:
        raise ValueError(f"{name} must be one safe directory name.")
    return value


@dataclass(slots=True, frozen=True)
class SubtitleSettings:
    secret_id: str
    secret_key: str
    region: str
    mps_host: str
    mps_version: str
    request_timeout: int
    poll_interval: float
    max_wait_seconds: int
    cos_bucket: str
    cos_output_prefix: str
    subtitle_definition: int
    oss_signed_url_expires: int

    @classmethod
    def from_env(cls) -> "SubtitleSettings":
        secret_id = (os.getenv("TENCENTCLOUD_SECRET_ID") or "").strip()
        secret_key = (os.getenv("TENCENTCLOUD_SECRET_KEY") or "").strip()
        if not secret_id or not secret_key:
            raise ValueError("Tencent credentials are required for subtitle-burn.")
        cos_bucket = (os.getenv("TENCENT_COS_BUCKET") or "goumee-1444407842").strip()
        if not cos_bucket:
            raise ValueError("TENCENT_COS_BUCKET is required for subtitle-burn.")
        return cls(
            secret_id=secret_id,
            secret_key=secret_key,
            region=(os.getenv("TENCENT_REGION") or "ap-guangzhou").strip(),
            mps_host=(os.getenv("TENCENT_MPS_HOST") or "mps.tencentcloudapi.com").strip(),
            mps_version=(os.getenv("TENCENT_MPS_VERSION") or "2019-06-12").strip(),
            request_timeout=_positive_int("TENCENT_REQUEST_TIMEOUT", 600, 5),
            poll_interval=_positive_float("TENCENT_POLL_INTERVAL", 5.0),
            max_wait_seconds=_positive_int("TENCENT_MAX_WAIT_SECONDS", 3600, 30),
            cos_bucket=cos_bucket,
            cos_output_prefix=_safe_subdir("TENCENT_COS_OUTPUT_PREFIX", "subtitle-output"),
            subtitle_definition=_positive_int("TENCENT_SUBTITLE_DEFINITION", 122),
            oss_signed_url_expires=_positive_int("SUBTITLE_OSS_SIGNED_URL_EXPIRES", 86400),
        )


def subtitle_input_subdir() -> str:
    return _safe_subdir("SUBTITLE_OSS_INPUT_SUBDIR", "subtitle-input")


def subtitle_output_subdir() -> str:
    return _safe_subdir("SUBTITLE_OSS_OUTPUT_SUBDIR", "subtitle-output")
