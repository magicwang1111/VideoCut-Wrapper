from __future__ import annotations

import subprocess
from types import SimpleNamespace
import pytest
from datetime import UTC, datetime
from urllib.parse import urlsplit

import videocut.oss.client as oss_client_module
from videocut.oss.client import OssClient


def test_output_key_includes_timestamp_folder_after_outputs(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OSS_LOCAL_ROOT", str(tmp_path / "oss"))
    monkeypatch.setenv("OSS_PREFIX", "GouMei-Video-Cut")

    oss = OssClient()

    assert (
        oss.output_key("t_c31d81520a654c94", datetime(2026, 5, 20, 14, 30, 12))
        == "GouMei-Video-Cut/outputs/20260520/20260520_143012/t_c31d81520a654c94/final.mp4"
    )


def test_output_key_converts_aware_timestamp_to_beijing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OSS_LOCAL_ROOT", str(tmp_path / "oss"))
    monkeypatch.setenv("OSS_PREFIX", "GouMei-Video-Cut")

    oss = OssClient()

    assert (
        oss.output_key("t_c31d81520a654c94", datetime(2026, 5, 20, 6, 30, 12, tzinfo=UTC))
        == "GouMei-Video-Cut/outputs/20260520/20260520_143012/t_c31d81520a654c94/final.mp4"
    )


def test_user_audio_key_uses_dedicated_prefix(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OSS_LOCAL_ROOT", str(tmp_path / "oss"))
    monkeypatch.setenv("OSS_PREFIX", "GouMei-Video-Cut")

    oss = OssClient()

    assert oss.user_audio_key("abc123def456", ".mp3") == "GouMei-Video-Cut/user-audio/abc123def456.mp3"


def test_public_url_uses_public_endpoint_and_preserves_path_slashes(monkeypatch) -> None:
    monkeypatch.delenv("OSS_LOCAL_ROOT", raising=False)
    monkeypatch.setenv("OSS_ENDPOINT", "oss-cn-hangzhou-internal.aliyuncs.com")
    monkeypatch.setenv("OSS_PUBLIC_ENDPOINT", "https://oss-cn-hangzhou.aliyuncs.com/")
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "test-id")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "test-secret")
    monkeypatch.setenv("OSS_BUCKET", "goumee-coze")

    oss = OssClient()

    url = oss.public_url("GouMei-Video-Cut/outputs/20260606/final video 中文.mp4")

    assert url == (
        "https://goumee-coze.oss-cn-hangzhou.aliyuncs.com/"
        "GouMei-Video-Cut/outputs/20260606/final%20video%20%E4%B8%AD%E6%96%87.mp4"
    )
    assert "%2F" not in url


def test_signed_get_url_uses_public_endpoint_for_external_consumers(monkeypatch) -> None:
    monkeypatch.delenv("OSS_LOCAL_ROOT", raising=False)
    monkeypatch.setenv("OSS_ENDPOINT", "oss-cn-hangzhou-internal.aliyuncs.com")
    monkeypatch.setenv("OSS_PUBLIC_ENDPOINT", "oss-cn-hangzhou.aliyuncs.com")
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "test-id")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "test-secret")
    monkeypatch.setenv("OSS_BUCKET", "goumee-coze")

    oss = OssClient()

    url = oss.signed_get_url("GouMei-Video-Cut/subtitle-input/sample.mp4")

    assert urlsplit(url).hostname == "goumee-coze.oss-cn-hangzhou.aliyuncs.com"
    assert "internal" not in url
    assert "/GouMei-Video-Cut/subtitle-input/sample.mp4" in url


def test_upload_defaults_to_ossutil64(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OSS_LOCAL_ROOT", raising=False)
    monkeypatch.delenv("OSS_UPLOAD_BACKEND", raising=False)
    monkeypatch.delenv("OSSUTIL_PATH", raising=False)
    monkeypatch.delenv("OSS_STS_TOKEN", raising=False)
    monkeypatch.setenv("OSS_ENDPOINT", "oss-cn-hangzhou.aliyuncs.com")
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "test-id")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "test-secret")
    monkeypatch.setenv("OSS_BUCKET", "goumee-coze")

    calls: list[tuple[list[str], int]] = []
    monkeypatch.setattr(oss_client_module.shutil, "which", lambda command: "/usr/local/bin/ossutil64" if command == "ossutil64" else None)

    def fake_run(command, **kwargs):
        calls.append((command, kwargs["timeout"]))
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(oss_client_module.subprocess, "run", fake_run)

    source = tmp_path / "final.mp4"
    source.write_bytes(b"video")
    oss = OssClient()
    monkeypatch.setattr(oss.bucket, "head_object", lambda key: SimpleNamespace(content_length=5, etag="etag", request_id="req"))
    oss.upload(source, "GouMei-Video-Cut/outputs/t_demo/final.mp4")

    assert calls == [
        (
            [
                "/usr/local/bin/ossutil64",
                "cp",
                str(source),
                "oss://goumee-coze/GouMei-Video-Cut/outputs/t_demo/final.mp4",
                "-e",
                "oss-cn-hangzhou.aliyuncs.com",
                "-i",
                "test-id",
                "-k",
                "test-secret",
                "-f",
            ],
            600,
        )
    ]


@pytest.mark.parametrize("remote_size", [None, 4])
def test_successful_tool_exit_requires_remote_object_and_matching_size(tmp_path, monkeypatch, remote_size):
    monkeypatch.setenv("OSS_LOCAL_ROOT", str(tmp_path / "oss"))
    oss = OssClient()
    oss.local_root = None
    oss.bucket = SimpleNamespace()
    def head(key):
        if remote_size is None:
            raise oss_client_module.oss2.exceptions.NoSuchKey(404, {}, {"Code": "NoSuchKey", "RequestId": "missing-request"})
        return SimpleNamespace(content_length=remote_size, etag="etag", request_id="size-request")
    oss.bucket.head_object = head
    monkeypatch.setattr(oss, "_upload_with_ossutil", lambda *args: {"exitCode": 0})
    monkeypatch.setenv("OSS_VERIFY_RETRY_DELAY_SECONDS", "0")
    source = tmp_path / "final.mp4"
    source.write_bytes(b"video")
    with pytest.raises(RuntimeError, match="Remote object verification failed"):
        oss.upload(source, "outputs/test/final.mp4")
    assert source.read_bytes() == b"video"


def test_verification_retries_temporary_timeout_and_returns_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("OSS_LOCAL_ROOT", str(tmp_path / "oss"))
    monkeypatch.setenv("OSS_VERIFY_RETRY_DELAY_SECONDS", "0")
    oss = OssClient()
    oss.local_root = None
    attempts = []
    def head(key):
        attempts.append(key)
        if len(attempts) == 1:
            raise TimeoutError("temporary timeout")
        return SimpleNamespace(content_length=5, etag="verified-etag", request_id="verified-request")
    oss.bucket = SimpleNamespace(head_object=head)
    monkeypatch.setattr(oss, "_upload_with_ossutil", lambda *args: {"exitCode": 0})
    source = tmp_path / "final.mp4"
    source.write_bytes(b"video")
    evidence = oss.upload(source, "outputs/final.mp4")
    assert evidence["verificationAttempt"] == 2
    assert evidence["outputAvailable"] is True
    assert evidence["remoteSizeBytes"] == 5
    assert evidence["etag"] == "verified-etag"
    assert evidence["requestId"] == "verified-request"


def test_verification_exhausted_timeout_does_not_report_success(tmp_path, monkeypatch):
    monkeypatch.setenv("OSS_LOCAL_ROOT", str(tmp_path / "oss"))
    monkeypatch.setenv("OSS_VERIFY_RETRY_DELAY_SECONDS", "0")
    oss = OssClient()
    oss.local_root = None
    def timeout(key):
        raise TimeoutError("HEAD timed out")
    oss.bucket = SimpleNamespace(head_object=timeout)
    monkeypatch.setattr(oss, "_upload_with_ossutil", lambda *args: {"exitCode": 0})
    source = tmp_path / "final.mp4"
    source.write_bytes(b"video")
    with pytest.raises(oss_client_module.OutputVerificationError) as failed:
        oss.upload(source, "outputs/final.mp4")
    assert failed.value.evidence["verificationAttempt"] == 3
    assert failed.value.evidence["outputAvailable"] is False
    assert source.exists()


def test_ossutil_timeout_traceback_does_not_expose_command_credentials(tmp_path, monkeypatch):
    import traceback
    monkeypatch.setenv("OSS_LOCAL_ROOT", str(tmp_path / "oss"))
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "test-private-id")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "test-private-secret")
    oss = OssClient()
    monkeypatch.setattr(oss, "_resolve_ossutil_path", lambda: "ossutil")
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 1)
    monkeypatch.setattr(oss_client_module.subprocess, "run", timeout)
    with pytest.raises(RuntimeError) as failed:
        oss._upload_with_ossutil(tmp_path / "final.mp4", "outputs/final.mp4")
    rendered = "".join(traceback.format_exception(failed.value))
    assert "test-private-secret" not in rendered
    assert "test-private-id" not in rendered
