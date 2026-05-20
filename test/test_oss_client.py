from __future__ import annotations

from datetime import datetime

from videocut.oss.client import OssClient


def test_output_key_includes_timestamp_folder_after_outputs(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OSS_LOCAL_ROOT", str(tmp_path / "oss"))
    monkeypatch.setenv("OSS_PREFIX", "GouMei-Video-Cut")

    oss = OssClient()

    assert (
        oss.output_key("t_c31d81520a654c94", datetime(2026, 5, 20, 14, 30, 12))
        == "GouMei-Video-Cut/outputs/20260520_143012/t_c31d81520a654c94/final.mp4"
    )
