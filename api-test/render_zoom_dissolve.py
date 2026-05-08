"""测试 zoom-dissolve-concat pipeline。

默认测试 group 1，不混 BGM，用来验证 zoom-dissolve 转场 pipeline。
运行:
python api-test/render_zoom_dissolve.py
"""

from __future__ import annotations

import os
from pathlib import Path

from test_runner import make_tester

API_BASE_URL = "http://127.0.0.1:3000"
API_KEY = os.getenv("API_KEY") or os.getenv("VIDEOCUT_API_KEY") or "change-me"
DOWNLOAD_DIR = Path(__file__).resolve().parent / "downloads"

PIPELINE = "zoom-dissolve-concat"
GROUP_IDS = [1]
BGM_FILE: str | None = None
DOWNLOAD = True


def main() -> int:
    make_tester(
        api_base_url=API_BASE_URL,
        api_key=API_KEY,
        pipeline=PIPELINE,
        group_ids=GROUP_IDS,
        bgm_file=BGM_FILE,
        download=DOWNLOAD,
        download_dir=DOWNLOAD_DIR,
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
