from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


SH = shutil.which("sh")


@pytest.mark.skipif(SH is None, reason="POSIX sh is required")
def test_entrypoint_syncs_template_bgm_on_startup(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ossutil = bin_dir / "ossutil"
    ossutil.write_text(
        '#!/usr/bin/env sh\nprintf "%s\\n" "$@" > "$OSSUTIL_LOG"\n',
        encoding="utf-8",
    )
    ossutil.chmod(0o755)

    log_path = tmp_path / "ossutil.log"
    template_dir = tmp_path / "bgm-templete"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
            "VIDEOCUT_ENV_FILE": str(tmp_path / "missing.env"),
            "OSS_ENDPOINT": "oss-cn-hangzhou.aliyuncs.com",
            "OSS_ACCESS_KEY_ID": "test-id",
            "OSS_ACCESS_KEY_SECRET": "test-secret",
            "OSSUTIL_LOG": str(log_path),
            "SYNC_BGM_ON_STARTUP": "0",
            "SYNC_BGM_TEMPLATE_ON_STARTUP": "1",
            "BGM_TEMPLATE_DIR": str(template_dir),
            "BGM_TEMPLATE_OSS_URI": "oss://test-bucket/bgm-templete/",
        }
    )

    result = subprocess.run(
        [SH, "docker/entrypoint.sh", SH, "-c", "exit 0"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert template_dir.is_dir()
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "sync",
        "oss://test-bucket/bgm-templete/",
        f"{template_dir}/",
        "-e",
        "oss-cn-hangzhou.aliyuncs.com",
        "-i",
        "test-id",
        "-k",
        "test-secret",
        "-u",
        "-f",
    ]


@pytest.mark.skipif(SH is None, reason="POSIX sh is required")
def test_entrypoint_can_disable_all_bgm_startup_sync(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.update(
        {
            "VIDEOCUT_ENV_FILE": str(tmp_path / "missing.env"),
            "SYNC_BGM_ON_STARTUP": "0",
            "SYNC_BGM_TEMPLATE_ON_STARTUP": "0",
        }
    )
    env.pop("OSS_ENDPOINT", None)
    env.pop("OSS_ACCESS_KEY_ID", None)
    env.pop("OSS_ACCESS_KEY_SECRET", None)

    result = subprocess.run(
        [SH, "docker/entrypoint.sh", SH, "-c", "exit 0"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "skip BGM sync because startup sync is disabled" in result.stdout
