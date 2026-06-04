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


def elapsed_summary(submitted_at: float | None, reached_95_at: float | None, completed_at: float | None = None) -> dict[str, float | None]:
    if submitted_at is None:
        return {
            "elapsedSeconds": None,
            "renderTo95Seconds": None,
            "uploadAfter95Seconds": None,
        }
    end = completed_at or time.time()
    render_to_95 = (reached_95_at - submitted_at) if reached_95_at is not None else None
    upload_after_95 = (end - reached_95_at) if reached_95_at is not None else None
    return {
        "elapsedSeconds": round(end - submitted_at, 2),
        "renderTo95Seconds": round(render_to_95, 2) if render_to_95 is not None else None,
        "uploadAfter95Seconds": round(upload_after_95, 2) if upload_after_95 is not None else None,
    }


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

    def poll_task(self, task_id: str, *, label: str | None = None, submitted_at: float | None = None) -> dict[str, Any]:
        deadline = time.time() + self.poll_timeout_seconds
        reached_95_at: float | None = None
        print(f"{prefix(label)}[poll] taskId={task_id}, timeout={self.poll_timeout_seconds}s")

        while True:
            task = self.get_task(task_id)
            status = task.get("status")
            progress = task.get("progress")
            if isinstance(progress, int) and progress >= 95 and reached_95_at is None:
                reached_95_at = time.time()
            attempt = task.get("attempt")
            output_url = task.get("outputUrl")
            error = task.get("error")
            print(
                f"{prefix(label)}[poll] status={status}, progress={progress}, attempt={attempt}, "
                f"outputUrl={output_url}, error={error}"
            )

            if status == "completed":
                task["_clientTiming"] = elapsed_summary(submitted_at, reached_95_at)
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

    def build_summary(
        self,
        *,
        group_id: int,
        task_id: str,
        task: dict[str, Any] | None,
        status: str | None = None,
        error: str | None = None,
        output_path: str | None = None,
        download_error: str | None = None,
        timing: dict[str, float | None] | None = None,
    ) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "label": f"group{group_id}",
            "group": group_id,
            "taskId": task_id,
            "status": status or (task.get("status") if task else None),
            "outputUrl": task.get("outputUrl") if task else None,
            "outputPath": output_path,
        }
        if timing:
            summary.update(timing)
        if error:
            summary["error"] = error
        if download_error:
            summary["downloadError"] = download_error
        return summary

    def complete_summary(self, group_id: int, task_id: str, task: dict[str, Any]) -> dict[str, Any]:
        label = f"group{group_id}"
        output_path = None
        download_error = None
        if task.get("status") == "completed" and self.download:
            try:
                output_path = str(self.download_task(task_id, group_id, label=label))
            except Exception as exc:
                download_error = str(exc)
        return self.build_summary(
            group_id=group_id,
            task_id=task_id,
            task=task,
            output_path=output_path,
            download_error=download_error,
            timing=task.get("_clientTiming") if isinstance(task.get("_clientTiming"), dict) else None,
        )

    def run_one(self, group_id: int) -> dict[str, Any]:
        label = f"group{group_id}"
        submitted_at = time.time()
        task_id = self.create_render_task(group_id, label=label)
        try:
            task = self.poll_task(task_id, label=label, submitted_at=submitted_at)
        except TimeoutError as exc:
            task = None
            try:
                task = self.get_task(task_id)
            except Exception:
                pass
            return self.build_summary(
                group_id=group_id,
                task_id=task_id,
                task=task,
                status="timeout",
                error=str(exc),
            )
        return self.complete_summary(group_id, task_id, task)

    def run_many(self) -> list[dict[str, Any]]:
        print(f"[batch] submitting {len(self.group_ids)} requests concurrently: groups={self.group_ids}")
        submitted: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=len(self.group_ids)) as executor:
            future_map = {
                executor.submit(self.create_render_task, group_id, label=f"group{group_id}"): group_id
                for group_id in self.group_ids
            }
            for future in as_completed(future_map):
                group_id = future_map[future]
                label = f"group{group_id}"
                try:
                    task_id = future.result()
                except Exception as exc:
                    results.append(
                        {
                            "label": label,
                            "group": group_id,
                            "status": "failed",
                            "error": str(exc),
                        }
                    )
                    continue
                submitted.append({"label": label, "group": group_id, "taskId": task_id, "submittedAt": time.time()})

        if submitted:
            results.extend(self.poll_submitted_tasks(submitted))
        return sorted(results, key=lambda item: int(item["group"]))

    def poll_submitted_tasks(self, submitted: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deadline = time.time() + self.poll_timeout_seconds
        remaining = {str(item["taskId"]): item for item in submitted}
        results: list[dict[str, Any]] = []
        reached_95_at: dict[str, float] = {}
        print(
            f"[batch][poll] tasks={len(remaining)}, timeout={self.poll_timeout_seconds}s, "
            f"interval={self.poll_interval_seconds}s"
        )

        while remaining:
            for task_id, item in list(remaining.items()):
                group_id = int(item["group"])
                label = str(item["label"])
                try:
                    task = self.get_task(task_id)
                except Exception as exc:
                    results.append(
                        self.build_summary(
                            group_id=group_id,
                            task_id=task_id,
                            task=None,
                            status="failed",
                            error=str(exc),
                        )
                    )
                    remaining.pop(task_id, None)
                    continue

                status = task.get("status")
                progress = task.get("progress")
                if isinstance(progress, int) and progress >= 95 and task_id not in reached_95_at:
                    reached_95_at[task_id] = time.time()
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
                    timing = elapsed_summary(
                        float(item["submittedAt"]) if "submittedAt" in item else None,
                        reached_95_at.get(task_id),
                    )
                    task["_clientTiming"] = timing
                    results.append(self.complete_summary(group_id, task_id, task))
                    remaining.pop(task_id, None)
                elif status == "failed":
                    results.append(
                        self.build_summary(
                            group_id=group_id,
                            task_id=task_id,
                            task=task,
                            error=f"task failed:\n{pretty(task)}",
                        )
                    )
                    remaining.pop(task_id, None)

            if not remaining:
                break
            if time.time() >= deadline:
                for task_id, item in sorted(remaining.items(), key=lambda entry: int(entry[1]["group"])):
                    group_id = int(item["group"])
                    task = None
                    try:
                        task = self.get_task(task_id)
                    except Exception:
                        pass
                    results.append(
                        self.build_summary(
                            group_id=group_id,
                            task_id=task_id,
                            task=task,
                            status="timeout",
                            error=f"poll timed out after {self.poll_timeout_seconds}s for task {task_id}",
                        )
                    )
                break
            time.sleep(self.poll_interval_seconds)

        return results

    def run(self) -> None:
        self.validate()
        self.print_config()
        self.test_health()
        if len(self.group_ids) == 1:
            summary = self.run_one(self.group_ids[0])
            print("[summary]")
            print(pretty(summary))
            return

        results = self.run_many()
        print("[summary]")
        print(pretty(results))

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
