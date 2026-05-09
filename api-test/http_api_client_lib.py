from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import sys
import time
from typing import Any

try:
    import requests
except ModuleNotFoundError as exc:  # pragma: no cover - import guard for local usage
    raise SystemExit(
        "Missing dependency: requests. Install it with `pip install requests` before running this script."
    ) from exc

from http_test_data import REAL_OSS_TEST_CLIP_GROUPS, validate_group_ids

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from videocut.config import REGISTERED_PIPELINE_NAMES  # noqa: E402


def pretty(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def prefix(label: str | None) -> str:
    return f"[{label}] " if label else ""


class VideoCutHttpTester:
    def __init__(
        self,
        *,
        api_base_url: str,
        api_key: str,
        download_dir: Path,
        request_timeout: int,
        poll_interval_seconds: float,
        poll_timeout_seconds: int,
        pipeline: str,
        group_ids: list[int],
        bgm_category: str | None,
        bgm_filename: str | None,
        download: bool,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.api_key = api_key
        self.download_dir = download_dir
        self.request_timeout = request_timeout
        self.poll_interval_seconds = poll_interval_seconds
        self.poll_timeout_seconds = poll_timeout_seconds
        self.pipeline = pipeline
        self.group_ids = group_ids
        self.bgm_category = bgm_category.strip() if isinstance(bgm_category, str) and bgm_category.strip() else None
        self.bgm_filename = bgm_filename.strip() if isinstance(bgm_filename, str) and bgm_filename.strip() else None
        self.download = download

    def headers(self, *, json_body: bool = False) -> dict[str, str]:
        headers = {"X-Api-Key": self.api_key}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def raise_for_error(self, response: requests.Response, label: str) -> None:
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
            f"Response: {pretty(body) if isinstance(body, (dict, list)) else body}"
        )

    def validate(self) -> None:
        if self.pipeline not in REGISTERED_PIPELINE_NAMES:
            raise RuntimeError(f"Pipeline not registered: {self.pipeline}")
        validate_group_ids(self.group_ids)

    def test_health(self) -> dict[str, Any]:
        url = f"{self.api_base_url}/health"
        print(f"[health] GET {url}")
        response = requests.get(url, timeout=self.request_timeout)
        self.raise_for_error(response, "health")
        data = response.json()
        print("[health] response:")
        print(pretty(data))
        return data

    def upload_local_file(self, file_path: str | Path) -> dict[str, Any]:
        path = Path(file_path).resolve()
        if not path.exists():
            raise FileNotFoundError(path)

        url = f"{self.api_base_url}/upload"
        print(f"[upload] POST {url}")
        print(f"[upload] file: {path}")
        with path.open("rb") as handle:
            response = requests.post(
                url,
                headers={"X-Api-Key": self.api_key},
                files={"file": (path.name, handle)},
                timeout=self.request_timeout,
            )
        self.raise_for_error(response, "upload")
        data = response.json()
        print("[upload] response:")
        print(pretty(data))
        return data

    def build_pipeline_payload(self, group_id: int) -> dict[str, Any]:
        overrides: dict[str, Any] = {}
        if self.bgm_category:
            overrides["bgm"] = {"category": self.bgm_category}
            if self.bgm_filename:
                overrides["bgm"]["filename"] = self.bgm_filename
        return {
            "pipeline": self.pipeline,
            "clips": REAL_OSS_TEST_CLIP_GROUPS[group_id],
            "overrides": overrides,
        }

    def create_render_task(self, group_id: int, *, label: str | None = None) -> str:
        payload = self.build_pipeline_payload(group_id)
        url = f"{self.api_base_url}/render"
        print(f"{prefix(label)}[render] POST {url}")
        print(f"{prefix(label)}[render] payload:")
        print(pretty(payload))
        response = requests.post(
            url,
            headers=self.headers(json_body=True),
            json=payload,
            timeout=self.request_timeout,
        )
        self.raise_for_error(response, "render")
        data = response.json()
        print(f"{prefix(label)}[render] response:")
        print(pretty(data))
        task_id = data.get("taskId")
        if not isinstance(task_id, str) or not task_id:
            raise RuntimeError(f"render response missing taskId: {pretty(data)}")
        return task_id

    def get_task(self, task_id: str) -> dict[str, Any]:
        url = f"{self.api_base_url}/tasks/{task_id}"
        response = requests.get(url, headers=self.headers(), timeout=self.request_timeout)
        self.raise_for_error(response, "get_task")
        return response.json()

    def poll_task(self, task_id: str, *, label: str | None = None) -> dict[str, Any]:
        deadline = time.time() + self.poll_timeout_seconds
        print(f"{prefix(label)}[poll] taskId={task_id}, timeout={self.poll_timeout_seconds}s")

        while True:
            task = self.get_task(task_id)
            status = task.get("status")
            progress = task.get("progress")
            attempt = task.get("attempt")
            output_url = task.get("outputUrl")
            error = task.get("error")
            print(
                f"{prefix(label)}[poll] status={status}, progress={progress}, attempt={attempt}, "
                f"outputUrl={output_url}, error={error}"
            )

            if status == "completed":
                print(f"{prefix(label)}[poll] final task response:")
                print(pretty(task))
                return task
            if status == "failed":
                raise RuntimeError(f"task failed:\n{pretty(task)}")
            if time.time() >= deadline:
                raise TimeoutError(f"poll timed out after {self.poll_timeout_seconds}s for task {task_id}")
            time.sleep(self.poll_interval_seconds)

    def download_task(self, task_id: str, group_id: int, *, label: str | None = None) -> Path:
        url = f"{self.api_base_url}/tasks/{task_id}/download"
        self.download_dir.mkdir(parents=True, exist_ok=True)
        target = self.download_dir / f"group{group_id}-{task_id}.mp4"

        print(f"{prefix(label)}[download] GET {url}")
        response = requests.get(
            url,
            headers=self.headers(),
            timeout=self.request_timeout,
            allow_redirects=True,
            stream=True,
        )
        self.raise_for_error(response, "download")

        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)

        print(f"{prefix(label)}[download] final_url={response.url}")
        print(f"{prefix(label)}[download] saved_to={target}")
        return target

    def run_one(self, group_id: int) -> dict[str, Any]:
        label = f"group{group_id}"
        task_id = self.create_render_task(group_id, label=label)
        task = self.poll_task(task_id, label=label)
        output_path = None
        if self.download:
            output_path = str(self.download_task(task_id, group_id, label=label))
        return {
            "label": label,
            "group": group_id,
            "taskId": task_id,
            "status": task.get("status"),
            "outputUrl": task.get("outputUrl"),
            "outputPath": output_path,
        }

    def run(self) -> None:
        self.validate()
        self.print_config()
        self.test_health()
        if len(self.group_ids) == 1:
            summary = self.run_one(self.group_ids[0])
            print("[summary]")
            print(pretty(summary))
            return

        print(f"[batch] submitting {len(self.group_ids)} requests concurrently: groups={self.group_ids}")
        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=len(self.group_ids)) as executor:
            future_map = {executor.submit(self.run_one, group_id): group_id for group_id in self.group_ids}
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
        print(pretty(sorted(results, key=lambda item: int(item["group"]))))

    def print_config(self) -> None:
        print(f"[config] API_BASE_URL={self.api_base_url}")
        print(f"[config] API_KEY_SET={bool(self.api_key and self.api_key != 'change-me')}")
        print(f"[config] pipeline={self.pipeline}, groups={self.group_ids}, download={self.download}")
        bgm_label = (
            f"{self.bgm_category}/{self.bgm_filename}"
            if self.bgm_category and self.bgm_filename
            else (self.bgm_category or "<random>")
        )
        print(f"[config] bgm={bgm_label}")
        print(f"[config] download_dir={self.download_dir}")
