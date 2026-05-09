"""测试随机 BGM 渲染。

默认测试 group 1，pipeline 使用 bgm-concat，不指定 BGM 分类，让服务递归随机选择音乐。
运行:
python api-test/render_bgm_random.py
"""

from __future__ import annotations

import os
from pathlib import Path

from test_runner import make_tester

API_BASE_URL = "http://127.0.0.1:3000"
API_KEY = os.getenv("API_KEY") or os.getenv("VIDEOCUT_API_KEY") or "change-me"
DOWNLOAD_DIR = Path(__file__).resolve().parent / "downloads"

PIPELINE = "bgm-concat"
GROUP_IDS = [1]
BGM_CATEGORY: str | None = None
BGM_FILENAME: str | None = None
DOWNLOAD = True


def main() -> int:
    make_tester(
        api_base_url=API_BASE_URL,
        api_key=API_KEY,
        pipeline=PIPELINE,
        group_ids=GROUP_IDS,
        bgm_category=BGM_CATEGORY,
        bgm_filename=BGM_FILENAME,
        download=DOWNLOAD,
        download_dir=DOWNLOAD_DIR,
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
