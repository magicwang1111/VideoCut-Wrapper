#!/usr/bin/env python3
"""Run the subtitle-burn OSS test with one selected narration BGM.

Default request:
    {
      "pipeline": "subtitle-burn",
      "clips": [
        "GouMei-Video-Cut/subtitle-input/Seedance_20260720_165432_00001_.mp4"
      ],
      "overrides": {
        "bgm": {
          "source": "bgm-avatar",
          "category": "口播测试",
          "filename": "1"
        }
      }
    }

Run:
    python api-test/render_subtitle_burn_oss_with_bgm.py

Optional environment overrides:
    SUBTITLE_OSS_KEY=GouMei-Video-Cut/subtitle-input/example.mp4
    SUBTITLE_BGM_SOURCE=bgm-avatar
    SUBTITLE_BGM_CATEGORY=口播测试
    SUBTITLE_BGM_FILENAME=1
    API_BASE_URL=http://127.0.0.1:3000
    DOWNLOAD=0

The complete health check, render submission, task polling, failure diagnostics,
and optional download flow is provided by the sibling subtitle OSS test.
"""

from __future__ import annotations

import os
import runpy
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent

os.environ.setdefault("SUBTITLE_BGM_SOURCE", "bgm-avatar")
os.environ.setdefault("SUBTITLE_BGM_CATEGORY", "口播测试")
os.environ.setdefault("SUBTITLE_BGM_FILENAME", "1")

runpy.run_path(str(SCRIPT_DIR / "render_subtitle_burn_oss.py"), run_name="__main__")
