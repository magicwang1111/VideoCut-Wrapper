from __future__ import annotations

import subprocess
from datetime import UTC, datetime

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
