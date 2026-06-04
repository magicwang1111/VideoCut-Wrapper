"""Upload a local file through the same OssClient path used by render workers.

Run:
FILE_PATH=/data/VideoCut-Wrapper/output/bgm-concat/final.mp4 REPEAT=4 CONCURRENCY=2 python api-test/diagnose_oss_upload.py
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import sys
import time
from typing import Any
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from videocut.oss import OssClient  # noqa: E402


FILE_PATH = Path(os.getenv("FILE_PATH", sys.argv[1] if len(sys.argv) > 1 else "")).expanduser()
REPEAT = max(1, int(os.getenv("REPEAT", "1")))
CONCURRENCY = max(1, int(os.getenv("CONCURRENCY", "1")))


def pretty(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def upload_once(index: int, file_path: Path) -> dict[str, Any]:
    oss = OssClient()
    task_id = f"diag_{uuid4().hex[:16]}_{index:03d}"
    oss_key = oss.output_key(task_id)
    start = time.time()
    oss.upload(file_path, oss_key)
    elapsed = time.time() - start
    return {
        "index": index,
        "status": "completed",
        "elapsedSeconds": round(elapsed, 3),
        "sizeBytes": file_path.stat().st_size,
        "backend": getattr(oss, "upload_backend", None),
        "endpoint": getattr(oss, "endpoint", None),
        "ossutilPath": getattr(oss, "ossutil_path", None),
        "ossKey": oss_key,
    }


def main() -> int:
    if not FILE_PATH.is_file():
        raise SystemExit("Set FILE_PATH to an existing local file, or pass the file path as argv[1].")
    max_workers = min(CONCURRENCY, REPEAT)
    print(
        pretty(
            {
                "filePath": str(FILE_PATH.resolve()),
                "sizeBytes": FILE_PATH.stat().st_size,
                "repeat": REPEAT,
                "concurrency": max_workers,
            }
        )
    )
    start = time.time()
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(upload_once, index, FILE_PATH): index for index in range(1, REPEAT + 1)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"index": index, "status": "failed", "error": str(exc)}
            print(pretty(result))
            results.append(result)
    print(
        pretty(
            {
                "totalElapsedSeconds": round(time.time() - start, 3),
                "results": sorted(results, key=lambda item: int(item["index"])),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
