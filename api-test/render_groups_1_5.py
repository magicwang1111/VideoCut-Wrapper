"""批量测试 1-5 组素材。

默认 pipeline 使用 bgm-concat，BGM 随机，下载 5 个结果文件。
运行:
python api-test/render_groups_1_5.py
"""

from __future__ import annotations

import os
from pathlib import Path

from test_runner import make_tester

API_BASE_URL = "http://127.0.0.1:3000"
API_KEY = os.getenv("API_KEY") or os.getenv("VIDEOCUT_API_KEY") or "change-me"
DOWNLOAD_DIR = Path(__file__).resolve().parent / "downloads"

PIPELINE = "bgm-concat"
GROUP_IDS = [1, 2, 3, 4, 5]
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
