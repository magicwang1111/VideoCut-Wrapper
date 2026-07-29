from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import videocut.aigc as aigc_module
from videocut.aigc import (
    AigcMetadataError,
    CONTENT_PROVIDER_CODE,
    build_aigc_metadata,
    embed_aigc_metadata,
    serialize_aigc_metadata,
)


def test_builds_complete_gb_45438_metadata() -> None:
    task_id = "0123456789abcdef"

    assert len(CONTENT_PROVIDER_CODE) == 27
    assert CONTENT_PROVIDER_CODE == "001191330401MA28AA78XT1VCUT"
    assert build_aigc_metadata(task_id) == {
        "Label": "1",
        "ContentProducer": CONTENT_PROVIDER_CODE,
        "ProduceID": "VCUT-0123456789abcdef",
        "ReservedCode1": "",
        "ContentPropagator": CONTENT_PROVIDER_CODE,
        "PropagateID": "VCUT-0123456789abcdef",
        "ReservedCode2": "",
    }
    assert serialize_aigc_metadata(task_id) == (
        '{"Label":"1","ContentProducer":"001191330401MA28AA78XT1VCUT",'
        '"ProduceID":"VCUT-0123456789abcdef","ReservedCode1":"",'
        '"ContentPropagator":"001191330401MA28AA78XT1VCUT",'
        '"PropagateID":"VCUT-0123456789abcdef","ReservedCode2":""}'
    )


def test_provider_code_contains_valid_unified_social_credit_identifier() -> None:
    credit_code = CONTENT_PROVIDER_CODE[4:22]
    characters = "0123456789ABCDEFGHJKLMNPQRTUWXY"
    weights = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)
    total = sum(characters.index(value) * weight for value, weight in zip(credit_code[:17], weights))
    expected_check = characters[(31 - total % 31) % 31]

    assert credit_code == "91330401MA28AA78XT"
    assert credit_code[-1] == expected_check
    assert CONTENT_PROVIDER_CODE[:4] == "0011"
    assert CONTENT_PROVIDER_CODE[22:] == "1VCUT"


def test_failed_write_preserves_original_file(tmp_path, monkeypatch) -> None:
    source = tmp_path / "final.mp4"
    source.write_bytes(b"original-video")

    def fail_write(command, **kwargs):
        raise AigcMetadataError("write failed")

    monkeypatch.setattr(aigc_module, "_run_process", fail_write)

    with pytest.raises(AigcMetadataError, match="write failed"):
        embed_aigc_metadata("ffmpeg", "ffprobe", source, "0123456789abcdef")

    assert source.read_bytes() == b"original-video"
    assert list(tmp_path.glob("*.aigc_tmp.mp4")) == []


def test_invalid_verification_preserves_original_file(tmp_path, monkeypatch) -> None:
    source = tmp_path / "final.mp4"
    source.write_bytes(b"original-video")
    calls = 0

    def fake_process(command, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            Path(command[-1]).write_bytes(b"remuxed-video")
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, json.dumps({"format": {"tags": {}}}), "")

    monkeypatch.setattr(aigc_module, "_run_process", fake_process)

    with pytest.raises(AigcMetadataError, match="did not find"):
        embed_aigc_metadata("ffmpeg", "ffprobe", source, "0123456789abcdef")

    assert source.read_bytes() == b"original-video"
    assert list(tmp_path.glob("*.aigc_tmp.mp4")) == []


def test_process_timeout_has_machine_readable_reason(monkeypatch) -> None:
    def timeout_process(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=10)

    monkeypatch.setattr(subprocess, "run", timeout_process)

    with pytest.raises(AigcMetadataError) as caught:
        aigc_module._run_process(
            ["ffmpeg"],
            timeout=10,
            label="AIGC FFmpeg metadata write",
            phase="write",
            failure_reason="AIGC_FFMPEG_FAILED",
            timeout_reason="AIGC_FFMPEG_TIMEOUT",
        )

    assert caught.value.reason == "AIGC_FFMPEG_TIMEOUT"
    assert caught.value.phase == "write"
    assert caught.value.retryable is True


def test_real_ffmpeg_round_trip_preserves_streams(tmp_path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg and ffprobe are required for the AIGC integration test.")

    source = tmp_path / "final.mp4"
    generated = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x120:rate=12",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=44100",
            "-t",
            "0.5",
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            "-metadata",
            'AIGC={"Label":"1","ContentProducer":"TENCENT","ProduceID":"upstream",'
            '"ContentPropagator":"1444407842","PropagateID":"upstream"}',
            "-movflags",
            "use_metadata_tags",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if generated.returncode != 0:
        pytest.skip(f"Local FFmpeg could not create the integration fixture: {generated.stderr[-500:]}")

    def probe_streams() -> list[dict[str, str]]:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_name,codec_type",
                "-of",
                "json",
                str(source),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return json.loads(result.stdout)["streams"]

    streams_before = probe_streams()
    metadata = embed_aigc_metadata(ffmpeg, ffprobe, source, "0123456789abcdef")
    streams_after = probe_streams()

    assert metadata == build_aigc_metadata("0123456789abcdef")
    assert streams_after == streams_before
    assert [item["codec_type"] for item in streams_after] == ["video", "audio"]
