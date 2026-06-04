from __future__ import annotations

import subprocess

import pytest

from videocut.errors import RenderError
from videocut.render.transitions import shared


def test_run_ffmpeg_checked_includes_label_and_stderr(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=218, stderr="filter failed near frame 12")

    monkeypatch.setattr(shared.subprocess, "run", fake_run)

    with pytest.raises(RenderError) as excinfo:
        shared.run_ffmpeg_checked(["ffmpeg", "-i", "clip.mp4"], timeout=600, label="normalize clip 1")

    message = str(excinfo.value)
    assert "normalize clip 1: FFmpeg exited with code 218." in message
    assert "filter failed near frame 12" in message
