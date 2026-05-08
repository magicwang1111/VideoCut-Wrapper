"""兼容旧入口：等同于 render_bgm_file.py。

推荐直接运行:
python api-test/render_bgm_file.py
"""

from __future__ import annotations

from render_bgm_file import main


if __name__ == "__main__":
    raise SystemExit(main())
