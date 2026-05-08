from __future__ import annotations

from test_runner import make_tester

GROUP_IDS = list(range(1, 17))


def main() -> int:
    make_tester(group_ids=GROUP_IDS, bgm_file=None, download=False).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
