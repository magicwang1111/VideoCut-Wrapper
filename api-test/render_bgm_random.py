from __future__ import annotations

from test_runner import make_tester


def main() -> int:
    make_tester(group_ids=[1], bgm_file=None, download=True).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
