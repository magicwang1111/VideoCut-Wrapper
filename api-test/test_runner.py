from __future__ import annotations

from pathlib import Path
from typing import cast

from http_api_client_lib import VideoCutHttpTester

_UNSET = object()


def make_tester(
    *,
    api_base_url: str,
    api_key: str,
    pipeline: str,
    group_ids: list[int],
    bgm_file: object = _UNSET,
    download: bool,
    download_dir: Path,
    request_timeout: int = 60,
    poll_interval_seconds: float = 5.0,
    poll_timeout_seconds: int = 1800,
) -> VideoCutHttpTester:
    selected_bgm_file = None if bgm_file is _UNSET else cast(str | None, bgm_file)
    return VideoCutHttpTester(
        api_base_url=api_base_url,
        api_key=api_key,
        download_dir=download_dir,
        request_timeout=request_timeout,
        poll_interval_seconds=poll_interval_seconds,
        poll_timeout_seconds=poll_timeout_seconds,
        pipeline=pipeline,
        group_ids=list(group_ids),
        bgm_file=selected_bgm_file,
        download=download,
    )
