from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    import requests
except ModuleNotFoundError as exc:  # pragma: no cover - import guard for local usage
    raise SystemExit(
        "Missing dependency: requests. Install it with `pip install requests` before running this script."
    ) from exc


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from videocut.config import REGISTERED_PIPELINE_NAMES  # noqa: E402


API_BASE_URL = os.getenv("API_BASE_URL") or os.getenv("VIDEOCUT_API_BASE_URL") or "http://127.0.0.1:3000"
API_KEY = os.getenv("API_KEY") or os.getenv("VIDEOCUT_API_KEY") or "change-me"
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR") or os.getenv("VIDEOCUT_DOWNLOAD_DIR") or (REPO_ROOT / "api-test" / "downloads"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "60"))
POLL_INTERVAL_SECONDS = float(os.getenv("POLL_INTERVAL_SECONDS", "5"))
POLL_TIMEOUT_SECONDS = int(os.getenv("POLL_TIMEOUT_SECONDS", "1800"))

DEFAULT_PIPELINE = "bgm-concat"
DEFAULT_PIPELINE_OVERRIDES: dict[str, Any] = {}

REAL_OSS_TEST_CLIP_GROUPS: dict[int, list[str]] = {
    1: [
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4390_0.mp4",
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4504_0.mp4",
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4567_0.mp4",
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4662_0.mp4",
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4663_0.mp4",
    ],
    2: [
        "GouMei-Video-Cut/test-input/2/kling_20260328_作品_镜头固定_原地展示穿_4643_0.mp4",
        "GouMei-Video-Cut/test-input/2/kling_20260328_作品_镜头固定_原地展示穿_4659_0.mp4",
        "GouMei-Video-Cut/test-input/2/kling_20260328_作品_镜头固定_原地展示穿_4662_0.mp4",
        "GouMei-Video-Cut/test-input/2/kling_20260328_作品_镜头固定_原地展示穿_4666_0-2.mp4",
        "GouMei-Video-Cut/test-input/2/kling_20260328_作品_镜头固定_原地展示穿_4666_0.mp4",
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4390_0.mp4",
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4504_0.mp4",
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4567_0.mp4",
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4662_0.mp4",
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4663_0.mp4",
    ],
    3: [
        "GouMei-Video-Cut/test-input/3/kling_20260328_作品_往前走_镜头固定_原_4520_0.mp4",
        "GouMei-Video-Cut/test-input/3/kling_20260328_作品_往前走_镜头固定_原_4535_0.mp4",
        "GouMei-Video-Cut/test-input/3/kling_20260328_作品_往前走_镜头固定_原_4538_0.mp4",
        "GouMei-Video-Cut/test-input/3/kling_20260328_作品_往前走_镜头固定_原_4541_0.mp4",
        "GouMei-Video-Cut/test-input/3/kling_20260328_作品_往前走_镜头固定_原_4545_0.mp4",
    ],
    4: [
        "GouMei-Video-Cut/test-input/4/kling_20260328_作品_原地展示穿搭_3338_0.mp4",
        "GouMei-Video-Cut/test-input/4/kling_20260328_作品_镜头固定_原地展示穿_3618_0.mp4",
        "GouMei-Video-Cut/test-input/4/kling_20260328_作品_镜头固定_原地展示穿_3620_0.mp4",
        "GouMei-Video-Cut/test-input/4/kling_20260328_作品_镜头固定_原地展示穿_3625_0.mp4",
        "GouMei-Video-Cut/test-input/4/kling_20260328_作品_镜头固定_原地展示穿_3630_0.mp4",
    ],
    5: [
        "GouMei-Video-Cut/test-input/5/kling_20260327_作品_原地展示穿搭_3512_0.mp4",
        "GouMei-Video-Cut/test-input/5/kling_20260327_作品_原地展示穿搭_3530_0.mp4",
        "GouMei-Video-Cut/test-input/5/kling_20260327_作品_原地展示穿搭_3553_0.mp4",
        "GouMei-Video-Cut/test-input/5/kling_20260327_作品_原地展示穿搭_3605_0.mp4",
        "GouMei-Video-Cut/test-input/5/kling_20260327_作品_原地展示穿搭_3645_0.mp4",
    ],
}


BASE_REAL_OSS_TEST_GROUP_IDS = tuple(sorted(REAL_OSS_TEST_CLIP_GROUPS))
MAX_GENERATED_GROUP_ID = 16


def _take_group_clips(group_id: int, start: int, count: int) -> list[str]:
    clips = REAL_OSS_TEST_CLIP_GROUPS[group_id]
    return [clips[(start + offset) % len(clips)] for offset in range(count)]


def _build_generated_group(group_id: int) -> list[str]:
    base_ids = BASE_REAL_OSS_TEST_GROUP_IDS
    offset = group_id - max(base_ids) - 1
    if group_id <= max(base_ids) + len(base_ids):
        primary = base_ids[offset % len(base_ids)]
        secondary = base_ids[(offset + 1) % len(base_ids)]
        return _take_group_clips(primary, offset, 3) + _take_group_clips(secondary, offset, 2)

    primary = base_ids[offset % len(base_ids)]
    secondary = base_ids[(offset + 2) % len(base_ids)]
    tertiary = base_ids[(offset + 4) % len(base_ids)]
    return (
        _take_group_clips(primary, offset, 2)
        + _take_group_clips(secondary, offset, 2)
        + _take_group_clips(tertiary, offset, 1)
    )


for generated_group_id in range(max(BASE_REAL_OSS_TEST_GROUP_IDS) + 1, MAX_GENERATED_GROUP_ID + 1):
    REAL_OSS_TEST_CLIP_GROUPS[generated_group_id] = _build_generated_group(generated_group_id)


def _headers(*, json_body: bool = False) -> dict[str, str]:
    headers = {"X-Api-Key": API_KEY}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _pretty(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _prefix(label: str | None) -> str:
    return f"[{label}] " if label else ""


def _raise_for_error(response: requests.Response, label: str) -> None:
    if response.ok:
        return
    body: Any
    try:
        body = response.json()
    except ValueError:
        body = response.text
    raise RuntimeError(
        f"{label} failed: HTTP {response.status_code}\n"
        f"URL: {response.request.method} {response.request.url}\n"
        f"Response: {_pretty(body) if isinstance(body, (dict, list)) else body}"
    )


def validate_registered_sources() -> None:
    if DEFAULT_PIPELINE not in REGISTERED_PIPELINE_NAMES:
        raise RuntimeError(f"Pipeline not registered: {DEFAULT_PIPELINE}")


def test_health() -> dict[str, Any]:
    url = f"{API_BASE_URL.rstrip('/')}/health"
    print(f"[health] GET {url}")
    response = requests.get(url, timeout=REQUEST_TIMEOUT)
    _raise_for_error(response, "health")
    data = response.json()
    print("[health] response:")
    print(_pretty(data))
    return data


def upload_local_file(file_path: str | Path) -> dict[str, Any]:
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)

    url = f"{API_BASE_URL.rstrip('/')}/upload"
    print(f"[upload] POST {url}")
    print(f"[upload] file: {path}")
    with path.open("rb") as handle:
        response = requests.post(
            url,
            headers={"X-Api-Key": API_KEY},
            files={"file": (path.name, handle)},
            timeout=REQUEST_TIMEOUT,
        )
    _raise_for_error(response, "upload")
    data = response.json()
    print("[upload] response:")
    print(_pretty(data))
    return data


def build_pipeline_payload(group_id: int) -> dict[str, Any]:
    return {
        "pipeline": DEFAULT_PIPELINE,
        "clips": REAL_OSS_TEST_CLIP_GROUPS[group_id],
        "overrides": DEFAULT_PIPELINE_OVERRIDES,
    }


def create_render_task(group_id: int, *, label: str | None = None) -> str:
    payload = build_pipeline_payload(group_id)
    url = f"{API_BASE_URL.rstrip('/')}/render"
    print(f"{_prefix(label)}[render] POST {url}")
    print(f"{_prefix(label)}[render] payload:")
    print(_pretty(payload))
    response = requests.post(
        url,
        headers=_headers(json_body=True),
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    _raise_for_error(response, "render")
    data = response.json()
    print(f"{_prefix(label)}[render] response:")
    print(_pretty(data))
    task_id = data.get("taskId")
    if not isinstance(task_id, str) or not task_id:
        raise RuntimeError(f"render response missing taskId: {_pretty(data)}")
    return task_id


def get_task(task_id: str) -> dict[str, Any]:
    url = f"{API_BASE_URL.rstrip('/')}/tasks/{task_id}"
    response = requests.get(url, headers=_headers(), timeout=REQUEST_TIMEOUT)
    _raise_for_error(response, "get_task")
    return response.json()


def poll_task(task_id: str, *, label: str | None = None) -> dict[str, Any]:
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    print(f"{_prefix(label)}[poll] taskId={task_id}, timeout={POLL_TIMEOUT_SECONDS}s")

    while True:
        task = get_task(task_id)
        status = task.get("status")
        progress = task.get("progress")
        attempt = task.get("attempt")
        output_url = task.get("outputUrl")
        error = task.get("error")
        print(
            f"{_prefix(label)}[poll] status={status}, progress={progress}, attempt={attempt}, "
            f"outputUrl={output_url}, error={error}"
        )

        if status == "completed":
            print(f"{_prefix(label)}[poll] final task response:")
            print(_pretty(task))
            return task
        if status == "failed":
            raise RuntimeError(f"task failed:\n{_pretty(task)}")
        if time.time() >= deadline:
            raise TimeoutError(f"poll timed out after {POLL_TIMEOUT_SECONDS}s for task {task_id}")
        time.sleep(POLL_INTERVAL_SECONDS)


def download_task(task_id: str, group_id: int, *, label: str | None = None) -> Path:
    url = f"{API_BASE_URL.rstrip('/')}/tasks/{task_id}/download"
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = DOWNLOAD_DIR / f"group{group_id}-{task_id}.mp4"

    print(f"{_prefix(label)}[download] GET {url}")
    response = requests.get(
        url,
        headers=_headers(),
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
        stream=True,
    )
    _raise_for_error(response, "download")

    with target.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                handle.write(chunk)

    print(f"{_prefix(label)}[download] final_url={response.url}")
    print(f"{_prefix(label)}[download] saved_to={target}")
    return target


def run_one(group_id: int, download: bool) -> dict[str, Any]:
    label = f"group{group_id}"
    task_id = create_render_task(group_id, label=label)
    task = poll_task(task_id, label=label)
    output_path = None
    if download:
        output_path = str(download_task(task_id, group_id, label=label))
    return {
        "label": label,
        "group": group_id,
        "taskId": task_id,
        "status": task.get("status"),
        "outputUrl": task.get("outputUrl"),
        "outputPath": output_path,
    }


def run(group_ids: list[int], download: bool) -> None:
    validate_registered_sources()
    test_health()
    if len(group_ids) == 1:
        summary = run_one(group_ids[0], download)
        print("[summary]")
        print(_pretty(summary))
        return

    print(f"[batch] submitting {len(group_ids)} requests concurrently: groups={group_ids}")
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(group_ids)) as executor:
        future_map = {executor.submit(run_one, group_id, download): group_id for group_id in group_ids}
        for future in as_completed(future_map):
            group_id = future_map[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(
                    {
                        "label": f"group{group_id}",
                        "group": group_id,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
    print("[summary]")
    print(_pretty(sorted(results, key=lambda item: int(item["group"]))))


def parse_group_ids(group: int, groups_text: str | None) -> list[int]:
    if not groups_text:
        return [group]
    group_ids: list[int] = []
    for raw in groups_text.split(","):
        value = raw.strip()
        if not value:
            continue
        if "-" in value:
            start_text, end_text = value.split("-", 1)
            start = int(start_text.strip())
            end = int(end_text.strip())
            if start > end:
                raise argparse.ArgumentTypeError(f"Invalid group range: {value}")
            candidates = range(start, end + 1)
        else:
            candidates = [int(value)]
        for group_id in candidates:
            if group_id not in REAL_OSS_TEST_CLIP_GROUPS:
                raise argparse.ArgumentTypeError(f"Unsupported group: {group_id}")
            group_ids.append(group_id)
    if not group_ids:
        raise argparse.ArgumentTypeError("No valid groups provided.")
    return group_ids


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VideoCut HTTP API real OSS test client.")
    parser.add_argument("--pipeline", default=DEFAULT_PIPELINE, help="Pipeline name to render (default: %(default)s)")
    parser.add_argument("--group", type=int, choices=sorted(REAL_OSS_TEST_CLIP_GROUPS), default=1)
    parser.add_argument(
        "--groups",
        help="Comma-separated group ids or ranges, e.g. 1,2,3 or 1-16. When set, requests are submitted concurrently.",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Poll task status but do not download the final output file.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    global DEFAULT_PIPELINE
    DEFAULT_PIPELINE = args.pipeline
    group_ids = parse_group_ids(args.group, args.groups)
    print(f"[config] API_BASE_URL={API_BASE_URL}")
    print(f"[config] API_KEY_SET={bool(API_KEY and API_KEY != 'change-me')}")
    print(f"[config] pipeline={DEFAULT_PIPELINE}, groups={group_ids}, download={not args.skip_download}")
    print(f"[config] download_dir={DOWNLOAD_DIR}")
    run(group_ids, download=not args.skip_download)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
