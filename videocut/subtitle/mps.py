from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import requests

from videocut.pipeline.types import PipelineSubtitleConfig
from videocut.subtitle.config import SubtitleSettings
from videocut.subtitle.tc3 import build_tc3_headers, canonical_json


@dataclass(slots=True)
class MpsTaskResult:
    task_id: str
    status: str
    subtitle_paths: list[str] = field(default_factory=list)
    message: str = ""


class MpsClient:
    def __init__(self, settings: SubtitleSettings) -> None:
        self.settings = settings

    def _post(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers, body = build_tc3_headers(
            secret_id=self.settings.secret_id, secret_key=self.settings.secret_key,
            service="mps", host=self.settings.mps_host, action=action,
            version=self.settings.mps_version, region=self.settings.region, payload=payload,
        )
        response = requests.post(
            f"https://{self.settings.mps_host}/", headers=headers,
            data=body.encode("utf-8"), timeout=self.settings.request_timeout,
        )
        try:
            decoded = response.json()
        except ValueError as exc:
            raise RuntimeError(f"{action} returned non-JSON HTTP {response.status_code}.") from exc
        top = decoded.get("Response")
        if response.status_code != 200 or not isinstance(top, dict):
            raise RuntimeError(f"{action} failed with HTTP {response.status_code}.")
        if isinstance(top.get("Error"), dict):
            error = top["Error"]
            raise RuntimeError(f"{action} failed: {error.get('Code')}: {error.get('Message')}")
        return top

    def submit(self, input_url: str, config: PipelineSubtitleConfig) -> str:
        user_ext: dict[str, Any] = {}
        if config.accurate_mode:
            user_ext["accurate_mode"] = 1
        if config.need_wordlist:
            user_ext["need_wordlist"] = 1
        if config.adapt_words.strip():
            user_ext["adapt_words"] = config.adapt_words.strip()
        if config.target_language.lower() != "auto":
            user_ext["translate_dst_language"] = config.target_language.lower()
        payload = {
            "InputInfo": {"Type": "URL", "UrlInputInfo": {"Url": input_url}},
            "SmartSubtitlesTask": {
                "Definition": config.definition or self.settings.subtitle_definition,
                "UserExtPara": canonical_json(user_ext) if user_ext else "",
            },
            "OutputStorage": {
                "Type": "COS",
                "CosOutputStorage": {"Bucket": self.settings.cos_bucket, "Region": self.settings.region},
            },
            "OutputDir": f"/{self.settings.cos_output_prefix}/",
        }
        result = self._post("ProcessMedia", payload)
        task_id = str(result.get("TaskId") or "").strip()
        if not task_id:
            raise RuntimeError("ProcessMedia returned no TaskId.")
        return task_id

    def describe(self, task_id: str) -> dict[str, Any]:
        return self._post("DescribeTaskDetail", {"TaskId": task_id})

    def wait(self, task_id: str, on_progress: Callable[[int], None] | None = None) -> MpsTaskResult:
        started = time.monotonic()
        while True:
            raw = self.describe(task_id)
            result = normalize_task_detail(task_id, raw)
            if on_progress is not None:
                elapsed_ratio = min(1.0, (time.monotonic() - started) / self.settings.max_wait_seconds)
                on_progress(20 + int(elapsed_ratio * 35))
            if result.status in {"SUCCESS", "FINISH", "FINISHED"}:
                if not result.subtitle_paths:
                    raise RuntimeError("MPS completed without a subtitle path.")
                return result
            if result.status in {"FAIL", "FAILED", "ABORTED"}:
                raise RuntimeError(result.message or "Tencent MPS subtitle task failed.")
            if time.monotonic() - started > self.settings.max_wait_seconds:
                raise TimeoutError(f"Tencent MPS task timed out after {self.settings.max_wait_seconds} seconds.")
            time.sleep(self.settings.poll_interval)


def _collect_paths(value: Any) -> list[str]:
    if isinstance(value, str):
        candidate = value.strip()
        return [candidate] if candidate.startswith(("http://", "https://", "/")) else []
    if isinstance(value, dict):
        return [item for child in value.values() for item in _collect_paths(child)]
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
        return [item for child in value for item in _collect_paths(child)]
    return []


def normalize_task_detail(task_id: str, raw: dict[str, Any]) -> MpsTaskResult:
    workflow = raw.get("WorkflowTask") if isinstance(raw.get("WorkflowTask"), dict) else {}
    status = str(raw.get("Status") or workflow.get("Status") or "UNKNOWN").upper()
    subtitle_paths: list[str] = []
    failures: list[str] = []
    for subtitle_task in workflow.get("SmartSubtitlesTaskResult") or []:
        if not isinstance(subtitle_task, dict):
            continue
        for name in ("AsrFullTextTask", "TransTextTask", "PureSubtitleTransTask", "OcrFullTextTask"):
            task = subtitle_task.get(name)
            if not isinstance(task, dict):
                continue
            if str(task.get("Status") or "").upper() in {"FAIL", "FAILED"}:
                failures.append(f"{name}: {task.get('ErrCodeExt') or task.get('ErrCode') or task.get('Message') or 'unknown'}")
            output = task.get("Output") if isinstance(task.get("Output"), dict) else {}
            path = output.get("SubtitlePath") or output.get("Path")
            if isinstance(path, str) and path.strip():
                subtitle_paths.append(path.strip())
    if failures:
        audio_streams = (workflow.get("MetaData") or {}).get("AudioStreamSet") if isinstance(workflow.get("MetaData"), dict) else None
        message = "Input video has no audio stream." if audio_streams == [] else "; ".join(failures)
        return MpsTaskResult(task_id, "FAILED", [], message)
    if not subtitle_paths:
        subtitle_paths = [path for path in _collect_paths(raw) if path.lower().split("?", 1)[0].endswith((".vtt", ".srt"))]
    return MpsTaskResult(task_id, status, list(dict.fromkeys(subtitle_paths)))
