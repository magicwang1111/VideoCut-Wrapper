"""Render one 5-clip OSS folder with segment-5-6-then-3-5-concat.

The API expects OSS keys, not full oss:// URIs. This script lists the folder
below, takes the first five video objects, submits one render task, polls it,
and downloads the result.

Run:
python api-test/render_segment_oss_5clips.py
"""

from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import sys
import time
from typing import Any
from urllib.parse import urlparse

try:
    import oss2
except ModuleNotFoundError as exc:  # pragma: no cover - import guard for local usage
    raise SystemExit("Missing dependency: oss2. Install project requirements before running this script.") from exc

try:
    import requests
except ModuleNotFoundError as exc:  # pragma: no cover - import guard for local usage
    raise SystemExit("Missing dependency: requests. Install it with `pip install requests`.") from exc

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from videocut.env import load_project_env  # noqa: E402
from videocut.runtime_paths import resolve_runtime_path  # noqa: E402

load_project_env(REPO_ROOT)

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
API_KEY = os.getenv("API_KEY") or os.getenv("VIDEOCUT_API_KEY") or "change-me"
DOWNLOAD_DIR = Path(__file__).resolve().parent / "downloads"

PIPELINE = "segment-5-6-then-3-5-concat"
OSS_TEST_URI = os.getenv(
    "OSS_TEST_URI",
    "oss://goumee-coze/GouMei-Video-Cut/test-input/\u6df7\u526a\u6d4b\u8bd5/",
)
CLIP_COUNT = int(os.getenv("CLIP_COUNT", "5"))
DOWNLOAD = os.getenv("DOWNLOAD", "1") != "0"
BGM_CATEGORY = "kpop"
BGM_FILENAME = "Hyperpop"
BGM_VOLUME = 1
BGM_FADE_OUT = 0
REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "60"))
POLL_INTERVAL_SECONDS = float(os.getenv("POLL_INTERVAL_SECONDS", "5"))
POLL_TIMEOUT_SECONDS = int(os.getenv("POLL_TIMEOUT_SECONDS", "3600"))
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


def pretty(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def parse_oss_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "oss" or not parsed.netloc:
        raise RuntimeError(f"OSS_TEST_URI must look like oss://bucket/prefix/: {uri}")
    prefix = parsed.path.lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return parsed.netloc, prefix


def is_video_key(key: str) -> bool:
    suffix = PurePosixPath(key).suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        return True
    mime, _ = mimetypes.guess_type(key)
    return bool(mime and mime.startswith("video/"))


def list_local_oss_keys(prefix: str) -> list[str]:
    local_root = os.getenv("OSS_LOCAL_ROOT")
    if not local_root:
        return []
    root = resolve_runtime_path(local_root, REPO_ROOT / "oss-local")
    base = root / Path(prefix)
    if not base.exists():
        raise RuntimeError(f"OSS_LOCAL_ROOT folder does not exist: {base}")
    keys: list[str] = []
    for path in base.rglob("*"):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            if is_video_key(rel):
                keys.append(rel)
    return sorted(keys)


def list_remote_oss_keys(bucket_name: str, prefix: str) -> list[str]:
    endpoint = os.getenv("OSS_ENDPOINT", "oss-cn-hangzhou.aliyuncs.com")
    access_key_id = os.getenv("OSS_ACCESS_KEY_ID")
    access_key_secret = os.getenv("OSS_ACCESS_KEY_SECRET")
    sts_token = os.getenv("OSS_STS_TOKEN")
    if not access_key_id or not access_key_secret:
        raise RuntimeError("Set OSS_ACCESS_KEY_ID and OSS_ACCESS_KEY_SECRET, or set OSS_LOCAL_ROOT for local testing.")

    auth = (
        oss2.StsAuth(access_key_id, access_key_secret, sts_token)
        if sts_token
        else oss2.Auth(access_key_id, access_key_secret)
    )
    bucket = oss2.Bucket(auth, endpoint, bucket_name)
    keys = [
        item.key
        for item in oss2.ObjectIterator(bucket, prefix=prefix)
        if not item.is_prefix() and is_video_key(item.key)
    ]
    return sorted(keys)


def list_video_keys_from_oss(uri: str) -> list[str]:
    bucket_name, prefix = parse_oss_uri(uri)
    configured_bucket = os.getenv("OSS_BUCKET", bucket_name)
    if configured_bucket != bucket_name:
        print(f"[warn] OSS_TEST_URI bucket={bucket_name}, OSS_BUCKET={configured_bucket}; using URI bucket.")
    keys = list_local_oss_keys(prefix)
    if keys:
        return keys
    return list_remote_oss_keys(bucket_name, prefix)


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


def test_health() -> None:
    url = f"{API_BASE_URL}/health"
    print(f"[health] GET {url}")
    response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
    raise_for_error(response, "health")
    print("[health] response:")
    print(pretty(response.json()))


def build_bgm_override() -> dict[str, Any]:
    bgm: dict[str, Any] = {
        "enabled": True,
        "volume": BGM_VOLUME,
        "fade_out": BGM_FADE_OUT,
    }
    if BGM_CATEGORY:
        bgm["category"] = BGM_CATEGORY
    if BGM_FILENAME:
        bgm["filename"] = BGM_FILENAME
    return bgm


def create_render_task(clips: list[str]) -> str:
    payload = {
        "pipeline": PIPELINE,
        "clips": clips,
        "overrides": {
            "bgm": build_bgm_override(),
        },
    }
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
    raise_for_error(response, "render")
    data = response.json()
    print("[render] response:")
    print(pretty(data))
    task_id = data.get("taskId")
    if not isinstance(task_id, str) or not task_id:
        raise RuntimeError(f"render response missing taskId: {pretty(data)}")
    return task_id


def get_task(task_id: str) -> dict[str, Any]:
    url = f"{API_BASE_URL}/tasks/{task_id}"
    response = requests.get(url, headers=headers(), timeout=REQUEST_TIMEOUT_SECONDS)
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
    url = f"{API_BASE_URL}/tasks/{task_id}/download"
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = DOWNLOAD_DIR / f"segment-5-6-then-3-5-{task_id}.mp4"
    print(f"[download] GET {url}")
    response = requests.get(
        url,
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
    print(f"[download] final_url={response.url}")
    print(f"[download] saved_to={target}")
    return target


def main() -> int:
    print(f"[config] API_BASE_URL={API_BASE_URL}")
    print(f"[config] API_KEY_SET={bool(API_KEY and API_KEY != 'change-me')}")
    print(f"[config] pipeline={PIPELINE}")
    print(f"[config] oss_uri={OSS_TEST_URI}")
    print(f"[config] clip_count={CLIP_COUNT}, download={DOWNLOAD}")
    print(f"[config] bgm_category={BGM_CATEGORY or '<random>'}, bgm_filename={BGM_FILENAME or '<random>'}")
    print(f"[config] bgm_volume={BGM_VOLUME}, bgm_fade_out={BGM_FADE_OUT}")

    keys = list_video_keys_from_oss(OSS_TEST_URI)
    selected = keys[:CLIP_COUNT]
    if len(selected) < CLIP_COUNT:
        raise RuntimeError(f"Only found {len(selected)} video object(s) under {OSS_TEST_URI}; need {CLIP_COUNT}.")

    print("[clips] selected:")
    print(pretty(selected))

    test_health()
    task_id = create_render_task(selected)
    task = poll_task(task_id)
    output_path = str(download_task(task_id)) if DOWNLOAD else None
    print("[summary]")
    print(
        pretty(
            {
                "taskId": task_id,
                "status": task.get("status"),
                "outputUrl": task.get("outputUrl"),
                "outputPath": output_path,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
