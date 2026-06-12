"""Test BGM category preflight behavior.

Default run checks that a missing BGM category is rejected by /render before a
task is created. Set BACKUP_BGM_CATEGORY to also submit a real render that uses
category-only random BGM selection from the backup BGM library.

Run:
python api-test/test_bgm_category_preflight.py

Default backup render category:
oss://goumee-coze/GouMei-Video-Cut/bgm-backup/卡点/
"""

from __future__ import annotations

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

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from http_test_data import REAL_OSS_TEST_CLIP_GROUPS, validate_group_ids  # noqa: E402
from videocut.env import load_env_file, load_project_env  # noqa: E402


DEFAULT_SERVER_ENV_FILE = "/data/env/videocut.env"

LOADED_ENV_FILES = [
    env_path
    for env_path in (
        os.getenv("VIDEOCUT_ENV_FILE"),
        DEFAULT_SERVER_ENV_FILE,
    )
    if env_path and load_env_file(env_path)
]
load_project_env(REPO_ROOT)


def first_csv_value(value: str | None) -> str | None:
    if not value:
        return None
    for item in value.split(","):
        item = item.strip()
        if item:
            return item
    return None


API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
API_KEY = os.getenv("API_KEY") or os.getenv("VIDEOCUT_API_KEY") or first_csv_value(os.getenv("API_KEYS")) or "change-me"
PIPELINE = os.getenv("PIPELINE", "bgm-concat")
GROUP_ID = int(os.getenv("GROUP_ID", "1"))
MISSING_BGM_CATEGORY = os.getenv("MISSING_BGM_CATEGORY", "20260612测试")
BACKUP_BGM_CATEGORY = os.getenv("BACKUP_BGM_CATEGORY", "卡点").strip()
DOWNLOAD = os.getenv("DOWNLOAD", "0") == "1"
DOWNLOAD_DIR = Path(__file__).resolve().parent / "downloads"
REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "60"))
POLL_INTERVAL_SECONDS = float(os.getenv("POLL_INTERVAL_SECONDS", "5"))
POLL_TIMEOUT_SECONDS = int(os.getenv("POLL_TIMEOUT_SECONDS", "1800"))


def pretty(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def headers(*, json_body: bool = False) -> dict[str, str]:
    values = {"X-Api-Key": API_KEY}
    if json_body:
        values["Content-Type"] = "application/json"
    return values


def response_body(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def raise_for_error(response: requests.Response, label: str) -> None:
    if response.ok:
        return
    body = response_body(response)
    raise RuntimeError(
        f"{label} failed: HTTP {response.status_code}\n"
        f"URL: {response.request.method} {response.request.url}\n"
        f"Response: {pretty(body) if isinstance(body, (dict, list)) else body}"
    )


def test_health() -> None:
    url = f"{API_BASE_URL}/health"
    print(f"[health] GET {url}")
    response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
    raise_for_error(response, "health")
    print("[health] response:")
    print(pretty(response.json()))


def build_payload(category: str) -> dict[str, Any]:
    validate_group_ids([GROUP_ID])
    return {
        "pipeline": PIPELINE,
        "clips": REAL_OSS_TEST_CLIP_GROUPS[GROUP_ID],
        "overrides": {
            "bgm": {
                "category": category,
            },
        },
    }


def post_render(payload: dict[str, Any]) -> requests.Response:
    url = f"{API_BASE_URL}/render"
    print(f"[render] POST {url}")
    print("[render] payload:")
    print(pretty(payload))
    response = requests.post(
        url,
        headers=headers(json_body=True),
        json=payload,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    print(f"[render] status={response.status_code}")
    print("[render] response:")
    print(pretty(response_body(response)))
    return response


def assert_missing_category_rejected() -> None:
    response = post_render(build_payload(MISSING_BGM_CATEGORY))
    body = response_body(response)
    if response.status_code != 400:
        raise RuntimeError(f"Expected HTTP 400 for missing category, got {response.status_code}.")
    if not isinstance(body, dict):
        raise RuntimeError(f"Expected JSON error body, got: {body}")
    if body.get("error_code") != 2007:
        raise RuntimeError(f"Expected error_code=2007, got:\n{pretty(body)}")
    details = body.get("details")
    if not isinstance(details, dict) or details.get("reason") != "category_not_found":
        raise RuntimeError(f"Expected details.reason=category_not_found, got:\n{pretty(body)}")
    if "taskId" in body:
        raise RuntimeError(f"Missing category must not return taskId:\n{pretty(body)}")
    print("[pass] missing category was rejected before task creation.")


def get_task(task_id: str) -> dict[str, Any]:
    response = requests.get(f"{API_BASE_URL}/tasks/{task_id}", headers=headers(), timeout=REQUEST_TIMEOUT_SECONDS)
    raise_for_error(response, "get_task")
    return response.json()


def poll_task(task_id: str) -> dict[str, Any]:
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    print(f"[poll] taskId={task_id}, timeout={POLL_TIMEOUT_SECONDS}s")
    while True:
        task = get_task(task_id)
        print(
            f"[poll] status={task.get('status')}, progress={task.get('progress')}, "
            f"attempt={task.get('attempt')}, outputUrl={task.get('outputUrl')}, error={task.get('error')}"
        )
        if task.get("status") == "completed":
            print("[poll] final task response:")
            print(pretty(task))
            return task
        if task.get("status") == "failed":
            raise RuntimeError(f"task failed:\n{pretty(task)}")
        if time.time() >= deadline:
            raise TimeoutError(f"poll timed out after {POLL_TIMEOUT_SECONDS}s for task {task_id}")
        time.sleep(POLL_INTERVAL_SECONDS)


def download_task(task_id: str) -> Path:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = DOWNLOAD_DIR / f"bgm-backup-category-{task_id}.mp4"
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
    print(f"[download] saved_to={target}")
    return target


def run_backup_category_render() -> None:
    response = post_render(build_payload(BACKUP_BGM_CATEGORY))
    raise_for_error(response, "backup category render")
    body = response.json()
    task_id = body.get("taskId")
    if not isinstance(task_id, str) or not task_id:
        raise RuntimeError(f"render response missing taskId:\n{pretty(body)}")
    print(f"[pass] backup-only category accepted, taskId={task_id}")
    task = poll_task(task_id)
    output_path = str(download_task(task_id)) if DOWNLOAD else None
    print("[summary]")
    print(
        pretty(
            {
                "taskId": task_id,
                "status": task.get("status"),
                "backupBgmCategory": BACKUP_BGM_CATEGORY,
                "outputUrl": task.get("outputUrl"),
                "outputPath": output_path,
            }
        )
    )


def main() -> int:
    print(f"[config] loaded_env_files={LOADED_ENV_FILES or '<none>'}")
    print(f"[config] API_BASE_URL={API_BASE_URL}")
    print(f"[config] API_KEY_SET={bool(API_KEY and API_KEY != 'change-me')}")
    print(f"[config] pipeline={PIPELINE}, group={GROUP_ID}")
    print(f"[config] missing_category={MISSING_BGM_CATEGORY}")
    print(f"[config] backup_category={BACKUP_BGM_CATEGORY or '<skip>'}, download={DOWNLOAD}")
    test_health()
    assert_missing_category_rejected()
    if BACKUP_BGM_CATEGORY:
        run_backup_category_render()
    else:
        print("[skip] BACKUP_BGM_CATEGORY is not set; backup random render was not submitted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
