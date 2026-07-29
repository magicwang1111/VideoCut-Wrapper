from __future__ import annotations

import json
import subprocess
from pathlib import Path
from uuid import uuid4


CONTENT_PROVIDER_CODE = "001191330401MA28AA78XT1VCUT"
PRODUCE_ID_PREFIX = "VCUT-"
_AIGC_FIELDS = (
    "Label",
    "ContentProducer",
    "ProduceID",
    "ReservedCode1",
    "ContentPropagator",
    "PropagateID",
    "ReservedCode2",
)


class AigcMetadataError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        reason: str = "AIGC_METADATA_UNKNOWN",
        phase: str = "unknown",
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.phase = phase
        self.retryable = retryable


def build_aigc_metadata(task_id: str) -> dict[str, str]:
    produce_id = f"{PRODUCE_ID_PREFIX}{task_id}"
    return {
        "Label": "1",
        "ContentProducer": CONTENT_PROVIDER_CODE,
        "ProduceID": produce_id,
        "ReservedCode1": "",
        "ContentPropagator": CONTENT_PROVIDER_CODE,
        "PropagateID": produce_id,
        "ReservedCode2": "",
    }


def serialize_aigc_metadata(task_id: str) -> str:
    return json.dumps(build_aigc_metadata(task_id), ensure_ascii=True, separators=(",", ":"))


def _run_process(
    command: list[str],
    *,
    timeout: int,
    label: str,
    phase: str,
    failure_reason: str,
    timeout_reason: str,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            check=False,
            timeout=timeout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise AigcMetadataError(
            f"{label} timed out after {timeout} seconds.",
            reason=timeout_reason,
            phase=phase,
        ) from exc
    except OSError as exc:
        raise AigcMetadataError(
            f"{label} could not start: {exc}",
            reason="AIGC_TOOL_UNAVAILABLE",
            phase="precheck",
            retryable=False,
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if len(detail) > 2000:
            detail = detail[-2000:]
        suffix = f"\n  {detail}" if detail else ""
        raise AigcMetadataError(
            f"{label} exited with code {result.returncode}.{suffix}",
            reason=failure_reason,
            phase=phase,
        )
    return result


def _read_aigc_metadata(ffprobe_path: str, video_path: Path, *, timeout: int) -> dict[str, str]:
    result = _run_process(
        [
            ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format_tags=AIGC",
            "-of",
            "json",
            str(video_path),
        ],
        timeout=timeout,
        label="AIGC ffprobe verification",
        phase="verify",
        failure_reason="AIGC_FFPROBE_FAILED",
        timeout_reason="AIGC_FFPROBE_TIMEOUT",
    )
    try:
        outer = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise AigcMetadataError(
            "AIGC ffprobe verification returned invalid probe JSON.",
            reason="AIGC_FFPROBE_OUTPUT_INVALID",
            phase="verify",
        ) from exc
    try:
        raw_aigc = outer["format"]["tags"]["AIGC"]
    except (KeyError, TypeError) as exc:
        raise AigcMetadataError(
            "AIGC ffprobe verification did not find the AIGC tag.",
            reason="AIGC_METADATA_MISSING",
            phase="verify",
        ) from exc
    if not isinstance(raw_aigc, str):
        raise AigcMetadataError(
            "AIGC ffprobe verification returned a non-string AIGC tag.",
            reason="AIGC_METADATA_JSON_INVALID",
            phase="verify",
        )
    try:
        metadata = json.loads(raw_aigc)
    except json.JSONDecodeError as exc:
        raise AigcMetadataError(
            "AIGC ffprobe verification returned invalid AIGC JSON.",
            reason="AIGC_METADATA_JSON_INVALID",
            phase="verify",
        ) from exc
    if not isinstance(metadata, dict) or tuple(metadata) != _AIGC_FIELDS:
        actual_fields = list(metadata) if isinstance(metadata, dict) else []
        raise AigcMetadataError(
            "AIGC ffprobe verification returned an unexpected field set or order: "
            f"expected={list(_AIGC_FIELDS)}, actual={actual_fields}.",
            reason="AIGC_METADATA_FIELDS_INVALID",
            phase="verify",
        )
    if not all(isinstance(value, str) for value in metadata.values()):
        raise AigcMetadataError(
            "AIGC ffprobe verification returned a non-string field value.",
            reason="AIGC_METADATA_FIELD_TYPE_INVALID",
            phase="verify",
        )
    return metadata


def embed_aigc_metadata(
    ffmpeg_path: str,
    ffprobe_path: str,
    video_path: str | Path,
    task_id: str,
    *,
    timeout: int = 600,
) -> dict[str, str]:
    source = Path(video_path)
    if not source.is_file() or source.stat().st_size == 0:
        raise AigcMetadataError(
            f"AIGC source video is missing or empty: {source}",
            reason="AIGC_SOURCE_MISSING_OR_EMPTY",
            phase="precheck",
            retryable=False,
        )
    if source.suffix.lower() != ".mp4":
        raise AigcMetadataError(
            f"AIGC metadata requires an MP4 output: {source}",
            reason="AIGC_SOURCE_NOT_MP4",
            phase="precheck",
            retryable=False,
        )

    expected = build_aigc_metadata(task_id)
    serialized = serialize_aigc_metadata(task_id)
    temporary = source.with_name(f"{source.name}.{uuid4().hex}.aigc_tmp.mp4")
    try:
        _run_process(
            [
                ffmpeg_path,
                "-v",
                "error",
                "-y",
                "-i",
                str(source),
                "-map",
                "0",
                "-c",
                "copy",
                "-metadata",
                f"AIGC={serialized}",
                "-movflags",
                "+faststart+use_metadata_tags",
                str(temporary),
            ],
            timeout=timeout,
            label="AIGC FFmpeg metadata write",
            phase="write",
            failure_reason="AIGC_FFMPEG_FAILED",
            timeout_reason="AIGC_FFMPEG_TIMEOUT",
        )
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise AigcMetadataError(
                "AIGC FFmpeg metadata write produced no output.",
                reason="AIGC_TEMP_OUTPUT_MISSING_OR_EMPTY",
                phase="write",
            )
        actual = _read_aigc_metadata(ffprobe_path, temporary, timeout=min(timeout, 60))
        if actual != expected:
            mismatched_fields = [field for field in _AIGC_FIELDS if actual.get(field) != expected[field]]
            raise AigcMetadataError(
                "AIGC ffprobe verification values do not match the requested metadata: "
                f"fields={mismatched_fields}.",
                reason="AIGC_METADATA_VALUES_MISMATCH",
                phase="verify",
            )
        try:
            temporary.replace(source)
        except OSError as exc:
            raise AigcMetadataError(
                f"AIGC atomic replacement failed: {exc}",
                reason="AIGC_ATOMIC_REPLACE_FAILED",
                phase="replace",
            ) from exc
        return actual
    finally:
        temporary.unlink(missing_ok=True)
