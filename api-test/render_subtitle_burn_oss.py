#!/usr/bin/env python3
"""Submit one OSS video to the subtitle-burn pipeline.

Server usage:
    python api-test/render_subtitle_burn_oss.py

Optional overrides:
    SUBTITLE_OSS_KEY=Happyhorse/subtitle-input/example.mp4 python api-test/render_subtitle_burn_oss.py
    API_BASE_URL=http://127.0.0.1:3000 DOWNLOAD=0 python api-test/render_subtitle_burn_oss.py

The server must provide the Tencent Cloud, Tencent COS, Alibaba OSS, and
VideoCut API settings. This script never embeds or prints secret values.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    import requests
except ModuleNotFoundError as exc:  # pragma: no cover - import guard for server usage
    raise SystemExit("Missing dependency: requests. Install the project requirements first.") from exc


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_SERVER_ENV_FILE = "/data/env/videocut.env"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from videocut.env import load_env_file, load_project_env  # noqa: E402


def load_runtime_env() -> list[Path]:
    """Load env files using the same precedence as the other API test scripts."""
    loaded: list[Path] = []
    for env_path in (os.getenv("VIDEOCUT_ENV_FILE"), DEFAULT_SERVER_ENV_FILE):
        if env_path and load_env_file(env_path):
            loaded.append(Path(env_path).expanduser().resolve())

    project_env = REPO_ROOT / ".env"
    if project_env.is_file():
        load_project_env(REPO_ROOT)
        loaded.append(project_env.resolve())

    return loaded


LOADED_ENV_FILES = load_runtime_env()


def resolve_api_key() -> str:
    direct = os.getenv("API_KEY") or os.getenv("VIDEOCUT_API_KEY")
    if direct:
        return direct.strip()

    configured = os.getenv("API_KEYS", "")
    first = next((item.strip() for item in configured.split(",") if item.strip()), "")
    return first or "change-me"


API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
API_KEY = resolve_api_key()
PIPELINE = "subtitle-burn"
SUBTITLE_OSS_KEY = os.getenv(
    "SUBTITLE_OSS_KEY",
    "Happyhorse/subtitle-input/Seedance_20260720_165432_00001_.mp4",
).strip()
DOWNLOAD = os.getenv("DOWNLOAD", "1").strip().lower() not in {"0", "false", "no"}
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "60"))
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "5"))
POLL_TIMEOUT = float(os.getenv("POLL_TIMEOUT", "3600"))
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", str(SCRIPT_DIR / "downloads")))


def headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY, "Content-Type": "application/json"}


def response_json(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"HTTP {response.status_code} returned non-JSON data: {response.text[:500]}"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"Expected a JSON object, got: {type(data).__name__}")
    return data


def check_health(session: requests.Session) -> None:
    response = session.get(f"{API_BASE_URL}/health", timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    print("Health:", json.dumps(response_json(response), ensure_ascii=False))


def submit_render(session: requests.Session) -> str:
    payload = {
        "pipeline": PIPELINE,
        "clips": [SUBTITLE_OSS_KEY],
        "overrides": {},
    }
    print("Request:", json.dumps(payload, ensure_ascii=False, indent=2))
    response = session.post(
        f"{API_BASE_URL}/render",
        headers=headers(),
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    if not response.ok:
        raise RuntimeError(f"Render failed: HTTP {response.status_code}: {response.text[:2000]}")

    result = response_json(response)
    task_id = str(result.get("taskId") or result.get("task_id") or "").strip()
    if not task_id:
        raise RuntimeError(f"Render response has no task ID: {result}")
    print(f"Task submitted: {task_id}")
    return task_id


def poll_task(session: requests.Session, task_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + POLL_TIMEOUT
    last_status = ""

    while time.monotonic() < deadline:
        response = session.get(
            f"{API_BASE_URL}/tasks/{task_id}",
            headers=headers(),
            timeout=REQUEST_TIMEOUT,
        )
        if not response.ok:
            raise RuntimeError(
                f"Task query failed: HTTP {response.status_code}: {response.text[:2000]}"
            )

        task = response_json(response)
        status = str(task.get("status", "unknown"))
        progress = task.get("progress")
        if status != last_status or progress is not None:
            progress_text = f", progress={progress}" if progress is not None else ""
            print(f"Task {task_id}: status={status}{progress_text}")
            last_status = status

        if status == "completed":
            return task
        if status in {"failed", "cancelled", "canceled"}:
            raise RuntimeError(
                "Subtitle render did not complete:\n"
                + json.dumps(task, ensure_ascii=False, indent=2)
            )

        time.sleep(POLL_INTERVAL)

    raise TimeoutError(f"Task {task_id} did not finish within {POLL_TIMEOUT:.0f} seconds")


def download_result(session: requests.Session, task_id: str) -> Path:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DOWNLOAD_DIR / f"subtitle-burn-{task_id}.mp4"
    with session.get(
        f"{API_BASE_URL}/tasks/{task_id}/download",
        headers=headers(),
        stream=True,
        timeout=(REQUEST_TIMEOUT, REQUEST_TIMEOUT),
    ) as response:
        if not response.ok:
            raise RuntimeError(
                f"Download failed: HTTP {response.status_code}: {response.text[:2000]}"
            )
        with output_path.open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    output.write(chunk)
    return output_path


def print_configuration() -> None:
    print("=== subtitle-burn API test ===")
    print(f"API_BASE_URL={API_BASE_URL}")
    print(f"API_KEY_SET={API_KEY != 'change-me'}")
    print(f"PIPELINE={PIPELINE}")
    print(f"SUBTITLE_OSS_KEY={SUBTITLE_OSS_KEY}")
    print(f"DOWNLOAD={DOWNLOAD}")
    print(f"POLL_TIMEOUT={POLL_TIMEOUT:.0f}s")
    if LOADED_ENV_FILES:
        print("ENV_FILES=" + ", ".join(str(path) for path in LOADED_ENV_FILES))
    else:
        print("ENV_FILES=(none)")
    print("Note: each run may create a new Tencent MPS task and incur cloud charges.")


def main() -> int:
    print_configuration()
    if not SUBTITLE_OSS_KEY:
        raise RuntimeError("SUBTITLE_OSS_KEY cannot be empty")

    started_at = time.monotonic()
    with requests.Session() as session:
        check_health(session)
        task_id = submit_render(session)
        task = poll_task(session, task_id)

        downloaded_path: Path | None = None
        if DOWNLOAD:
            downloaded_path = download_result(session, task_id)

    summary = {
        "taskId": task_id,
        "status": task.get("status"),
        "outputUrl": task.get("outputUrl") or task.get("output_url"),
        "outputPath": task.get("outputPath") or task.get("output_path"),
        "downloadedPath": str(downloaded_path.resolve()) if downloaded_path else None,
        "elapsedSeconds": round(time.monotonic() - started_at, 2),
    }
    print("Result:")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (requests.RequestException, RuntimeError, TimeoutError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
