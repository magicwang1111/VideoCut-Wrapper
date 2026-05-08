from __future__ import annotations

from test_runner import make_tester


def main() -> int:
    tester = make_tester(download=False)
    tester.print_config()
    tester.test_health()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
