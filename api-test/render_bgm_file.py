from __future__ import annotations

from test_runner import make_tester

BGM_FILE = "舒缓/1.mp3"


def main() -> int:
    make_tester(group_ids=[1], bgm_file=BGM_FILE, download=True).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
