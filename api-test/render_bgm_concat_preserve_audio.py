#!/usr/bin/env python3
"""Run one bgm-concat API test that preserves and mixes the input audio.

Defaults:
    clip: GouMei-Video-Cut/subtitle-input/Seedance_20260720_165432_00001_.mp4
    BGM:  bgm-avatar / 口播测试 / 走播音乐

Run on the server:
    python api-test/render_bgm_concat_preserve_audio.py

Optional overrides:
    API_BASE_URL=http://127.0.0.1:3000 DOWNLOAD=0 \
        python api-test/render_bgm_concat_preserve_audio.py
    BGM_SOURCE=template BGM_CATEGORY=测试1 BGM_FILENAME=生活感 \
        python api-test/render_bgm_concat_preserve_audio.py

The script performs the health check, submits one task, polls it, prints
failure diagnostics, and downloads the completed result by default.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_SERVER_ENV_FILE = "/data/env/videocut.env"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from videocut.env import load_env_file, load_project_env  # noqa: E402


for env_path in (os.getenv("VIDEOCUT_ENV_FILE"), DEFAULT_SERVER_ENV_FILE):
    if env_path:
        load_env_file(env_path)
if (REPO_ROOT / ".env").is_file():
    load_project_env(REPO_ROOT)

if not (os.getenv("API_KEY") or os.getenv("VIDEOCUT_API_KEY")):
    api_key = next((item.strip() for item in os.getenv("API_KEYS", "").split(",") if item.strip()), "")
    if api_key:
        os.environ["API_KEY"] = api_key

os.environ.setdefault("PIPELINE", "bgm-concat")
os.environ.setdefault(
    "SINGLE_VIDEO_OSS_KEY",
    "GouMei-Video-Cut/subtitle-input/Seedance_20260720_165432_00001_.mp4",
)
os.environ.setdefault("REQUEST_COUNT", "1")
os.environ.setdefault("CONCURRENCY", "1")
os.environ.setdefault("BGM_SOURCE", "bgm-avatar")
os.environ.setdefault("BGM_CATEGORY", "口播测试")
os.environ.setdefault("BGM_FILENAME", "走播音乐")
os.environ.setdefault("DOWNLOAD", "1")

runpy.run_path(str(SCRIPT_DIR / "render_single_video_stress.py"), run_name="__main__")
