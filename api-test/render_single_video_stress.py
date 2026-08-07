"""Stress test with one video clip per render task.

This script does not use test groups. It submits many render tasks that all
reference the same OSS video key, which is useful for queue, worker, OSS, and
single-video BGM pressure testing.

Run:
API_BASE_URL=http://127.0.0.1:3030 REQUEST_COUNT=16 CONCURRENCY=8 DOWNLOAD=0 python api-test/render_single_video_stress.py
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

try:
    import requests
except ModuleNotFoundError as exc:  # pragma: no cover - import guard for local usage
    raise SystemExit("Missing dependency: requests. Install it with `pip install requests`.") from exc

from http_test_data import REAL_OSS_TEST_CLIP_GROUPS

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
API_KEY = os.getenv("API_KEY") or os.getenv("VIDEOCUT_API_KEY") or "change-me"
DOWNLOAD_DIR = Path(__file__).resolve().parent / "downloads"

PIPELINE = os.getenv("PIPELINE", "bgm-concat")
SINGLE_VIDEO_OSS_KEY = os.getenv("SINGLE_VIDEO_OSS_KEY", REAL_OSS_TEST_CLIP_GROUPS[1][0])
REQUEST_COUNT = int(os.getenv("REQUEST_COUNT", "16"))
CONCURRENCY = max(1, int(os.getenv("CONCURRENCY", "8")))
BGM_CATEGORY = os.getenv("BGM_CATEGORY", "").strip() or None
BGM_FILENAME = os.getenv("BGM_FILENAME", "").strip() or None
BGM_SOURCE = os.getenv("BGM_SOURCE", "").strip() or None
DOWNLOAD = os.getenv("DOWNLOAD", "0").strip().lower() not in {"0", "false", "no", "off"}
REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "60"))
POLL_INTERVAL_SECONDS = float(os.getenv("POLL_INTERVAL_SECONDS", "5"))
POLL_TIMEOUT_SECONDS = int(os.getenv("POLL_TIMEOUT_SECONDS", "7200"))


def pretty(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def headers(*, json_body: bool = False) -> dict[str, str]:
    values = {"X-Api-Key": API_KEY}
    if json_body:
        values["Content-Type"] = "application/json"
    return values


def raise_for_error(response: requests.Response, label: str) -> None:
    if response.ok:
        return
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text
    raise RuntimeError(
        f"{label} failed: HTTP {response.status_code}\n"
        f"URL: {response.request.method} {response.request.url}\n"
        f"Response: {pretty(body) if isinstance(body, (dict, list)) else body}"
    )


def build_payload() -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if BGM_SOURCE or BGM_CATEGORY:
        overrides["bgm"] = {}
        if BGM_SOURCE:
            overrides["bgm"]["source"] = BGM_SOURCE
        if BGM_CATEGORY:
            overrides["bgm"]["category"] = BGM_CATEGORY
        if BGM_FILENAME:
            overrides["bgm"]["filename"] = BGM_FILENAME
    return {
        "pipeline": PIPELINE,
        "clips": [SINGLE_VIDEO_OSS_KEY],
        "overrides": overrides,
    }


def test_health() -> None:
    url = f"{API_BASE_URL}/health"
    print(f"[health] GET {url}")
    response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
    raise_for_error(response, "health")
    print("[health] response:")
    print(pretty(response.json()))


def create_render_task(index: int) -> dict[str, Any]:
    payload = build_payload()
    url = f"{API_BASE_URL}/render"
    label = f"single{index:03d}"
    print(f"[{label}][render] POST {url}")
    response = requests.post(
        url,
        headers=headers(json_body=True),
        json=payload,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    raise_for_error(response, "render")
    data = response.json()
    task_id = data.get("taskId")
    if not isinstance(task_id, str) or not task_id:
        raise RuntimeError(f"render response missing taskId: {pretty(data)}")
    print(f"[{label}][render] taskId={task_id}")
    return {"label": label, "taskId": task_id, "submittedAt": time.time()}


def get_task(task_id: str) -> dict[str, Any]:
    response = requests.get(f"{API_BASE_URL}/tasks/{task_id}", headers=headers(), timeout=REQUEST_TIMEOUT_SECONDS)
    raise_for_error(response, "get_task")
    return response.json()


def download_task(label: str, task_id: str) -> str:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = DOWNLOAD_DIR / f"{label}-{task_id}.mp4"
    response = requests.get(
        f"{API_BASE_URL}/tasks/{task_id}/download",
        headers=headers(),
        timeout=REQUEST_TIMEOUT_SECONDS,
        allow_redirects=True,
        stream=True,
    )
    raise_for_error(response, "download")
    with target.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                handle.write(chunk)
    return str(target)


def poll_submitted_tasks(submitted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    remaining = {str(item["taskId"]): item for item in submitted}
    results: list[dict[str, Any]] = []
    reached_95_at: dict[str, float] = {}

    print(
        f"[poll] tasks={len(remaining)}, timeout={POLL_TIMEOUT_SECONDS}s, "
        f"interval={POLL_INTERVAL_SECONDS}s"
    )
    while remaining:
        for task_id, item in list(remaining.items()):
            label = str(item["label"])
            try:
                task = get_task(task_id)
            except Exception as exc:
                results.append({"label": label, "taskId": task_id, "status": "failed", "error": str(exc)})
                remaining.pop(task_id, None)
                continue

            status = task.get("status")
            progress = task.get("progress")
            upload_diagnostics = task.get("uploadDiagnostics")
            if isinstance(progress, int) and progress >= 95 and task_id not in reached_95_at:
                reached_95_at[task_id] = time.time()
            print(
                f"[{label}][poll] status={status}, progress={progress}, "
                f"attempt={task.get('attempt')}, uploadDiagnostics={upload_diagnostics}, "
                f"error={task.get('error')}"
            )
            if status == "completed":
                output_path = None
                download_error = None
                if DOWNLOAD:
                    try:
                        output_path = download_task(label, task_id)
                    except Exception as exc:
                        download_error = str(exc)
                elapsed = time.time() - float(item["submittedAt"])
                render_to_95 = None
                upload_after_95 = None
                if task_id in reached_95_at:
                    render_to_95 = reached_95_at[task_id] - float(item["submittedAt"])
                    upload_after_95 = time.time() - reached_95_at[task_id]
                summary = {
                    "label": label,
                    "taskId": task_id,
                    "status": status,
                    "elapsedSeconds": round(elapsed, 2),
                    "renderTo95Seconds": round(render_to_95, 2) if render_to_95 is not None else None,
                    "after95ObservedSeconds": round(upload_after_95, 2) if upload_after_95 is not None else None,
                    "uploadDiagnostics": upload_diagnostics,
                    "outputUrl": task.get("outputUrl"),
                    "outputPath": output_path,
                }
                if download_error:
                    summary["downloadError"] = download_error
                results.append(summary)
                remaining.pop(task_id, None)
            elif status == "failed":
                results.append(
                    {
                        "label": label,
                        "taskId": task_id,
                        "status": status,
                        "error": task.get("error"),
                        "lastError": task.get("lastError"),
                        "uploadDiagnostics": upload_diagnostics,
                    }
                )
                remaining.pop(task_id, None)

        if not remaining:
            break
        if time.time() >= deadline:
            for task_id, item in sorted(remaining.items(), key=lambda entry: str(entry[1]["label"])):
                results.append(
                    {
                        "label": item["label"],
                        "taskId": task_id,
                        "status": "timeout",
                        "error": f"poll timed out after {POLL_TIMEOUT_SECONDS}s",
                    }
                )
            break
        time.sleep(POLL_INTERVAL_SECONDS)

    return sorted(results, key=lambda item: str(item["label"]))


def main() -> int:
    if BGM_SOURCE not in {None, "catalog", "template", "bgm-avatar"}:
        raise RuntimeError("BGM_SOURCE must be catalog, template, or bgm-avatar")
    if BGM_SOURCE in {"template", "bgm-avatar"} and (not BGM_CATEGORY or not BGM_FILENAME):
        raise RuntimeError("BGM_CATEGORY and BGM_FILENAME are required for template and bgm-avatar")
    print(f"[config] API_BASE_URL={API_BASE_URL}")
    print(f"[config] API_KEY_SET={bool(API_KEY and API_KEY != 'change-me')}")
    print(f"[config] pipeline={PIPELINE}")
    print(f"[config] single_video_oss_key={SINGLE_VIDEO_OSS_KEY}")
    print(f"[config] request_count={REQUEST_COUNT}, concurrency={CONCURRENCY}, download={DOWNLOAD}")
    bgm_name = BGM_CATEGORY + "/" + BGM_FILENAME if BGM_CATEGORY and BGM_FILENAME else (BGM_CATEGORY or "<random>")
    print(f"[config] bgm={(BGM_SOURCE + '/') if BGM_SOURCE else ''}{bgm_name}")

    test_health()
    max_workers = min(CONCURRENCY, REQUEST_COUNT)
    submitted: list[dict[str, Any]] = []
    submit_results: list[dict[str, Any]] = []
    start = time.time()

    print(f"[submit] request_count={REQUEST_COUNT}, max_workers={max_workers}")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(create_render_task, index): index for index in range(1, REQUEST_COUNT + 1)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                submitted.append(future.result())
            except Exception as exc:
                submit_results.append({"label": f"single{index:03d}", "status": "failed", "error": str(exc)})

    results = submit_results + poll_submitted_tasks(submitted)
    total_elapsed = round(time.time() - start, 2)
    print("[summary]")
    print(
        pretty(
            {
                "totalElapsedSeconds": total_elapsed,
                "requestCount": REQUEST_COUNT,
                "concurrency": max_workers,
                "submitted": len(submitted),
                "results": results,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
