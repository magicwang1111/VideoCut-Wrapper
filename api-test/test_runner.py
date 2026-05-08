from __future__ import annotations

from typing import cast

from http_api_client_lib import VideoCutHttpTester
import http_api_settings as settings

_UNSET = object()


def make_tester(
    *,
    pipeline: str | None = None,
    group_ids: list[int] | None = None,
    bgm_file: object = _UNSET,
    download: bool | None = None,
) -> VideoCutHttpTester:
    selected_bgm_file = settings.BGM_FILE if bgm_file is _UNSET else cast(str | None, bgm_file)
    return VideoCutHttpTester(
        api_base_url=settings.API_BASE_URL,
        api_key=settings.API_KEY,
        download_dir=settings.DOWNLOAD_DIR,
        request_timeout=settings.REQUEST_TIMEOUT,
        poll_interval_seconds=settings.POLL_INTERVAL_SECONDS,
        poll_timeout_seconds=settings.POLL_TIMEOUT_SECONDS,
        pipeline=pipeline or settings.PIPELINE,
        group_ids=list(group_ids or settings.GROUPS),
        bgm_file=selected_bgm_file,
        download=settings.DOWNLOAD if download is None else download,
    )
