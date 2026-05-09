"""只测试服务是否启动正常。

运行:
python api-test/check_health.py
"""

from __future__ import annotations

import os
from pathlib import Path

from test_runner import make_tester

API_BASE_URL = "http://127.0.0.1:3000"
API_KEY = os.getenv("API_KEY") or os.getenv("VIDEOCUT_API_KEY") or "change-me"
DOWNLOAD_DIR = Path(__file__).resolve().parent / "downloads"


def main() -> int:
    tester = make_tester(
        api_base_url=API_BASE_URL,
        api_key=API_KEY,
        pipeline="bgm-concat",
        group_ids=[1],
        bgm_category=None,
        bgm_filename=None,
        download=False,
        download_dir=DOWNLOAD_DIR,
    )
    tester.print_config()
    tester.test_health()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
