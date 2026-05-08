from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 常用联调配置都改这里。也可以直接运行单功能脚本:
# python api-test/render_bgm_file.py
API_BASE_URL = "http://127.0.0.1:3000"
API_KEY = os.getenv("API_KEY") or os.getenv("VIDEOCUT_API_KEY") or "change-me"

PIPELINE = "bgm-concat"
GROUPS = [1]

# 指定 /app/input/bgm 下的相对路径；设为 None 时随机选择 BGM。
BGM_FILE: str | None = "舒缓/1.mp3"

DOWNLOAD = True
DOWNLOAD_DIR = REPO_ROOT / "api-test" / "downloads"

REQUEST_TIMEOUT = 60
POLL_INTERVAL_SECONDS = 5.0
POLL_TIMEOUT_SECONDS = 1800
