from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from videocut.oss import OssClient
from videocut.pipeline.types import PipelineSubtitleConfig
from videocut.subtitle.ass import write_ass
from videocut.subtitle.burn import burn_ass
from videocut.subtitle.config import SubtitleSettings
from videocut.subtitle.cos import download_subtitle
from videocut.subtitle.mps import MpsClient, MpsTaskResult
from videocut.subtitle.parser import parse_subtitle, select_language, wrap_cues

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SubtitleRunResult:
    output_path: str
    mps_task_id: str
    subtitle_path: str
    cue_count: int
    encoder: str
    metadata: dict[str, object] = field(default_factory=dict)


class SubtitlePipelineRunner:
    def __init__(self, root_dir: str | Path, oss: OssClient | None = None) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.oss = oss or OssClient()

    def run(self, *, task_id: str, source_oss_key: str, local_input: str | Path,
            task_dir: str | Path, config: PipelineSubtitleConfig, ffmpeg_path: str,
            ffprobe_path: str, existing_state: dict[str, object] | None = None,
            attempt: int | None = None,
            on_progress: Callable[[int], None] | None = None,
            on_external_job: Callable[[dict[str, object]], None] | None = None) -> SubtitleRunResult:
        settings = SubtitleSettings.from_env()
        task_path = Path(task_dir)
        state = dict(existing_state or {})
        mps = MpsClient(settings)
        progress = on_progress or (lambda _: None)
        progress(15)

        mps_task_id = str(state.get("mps_task_id") or "").strip()
        if not mps_task_id:
            signed_url = self.oss.signed_get_url(source_oss_key, settings.oss_signed_url_expires)
            mps_task_id = mps.submit(signed_url, config)
            state["mps_task_id"] = mps_task_id
            if on_external_job is not None:
                on_external_job({
                    "external_task_id": mps_task_id,
                    "submitted_attempt": attempt,
                    "status": "submitted",
                    "persist_state": True,
                })
            logger.info("subtitle stage=mps_submitted task_id=%s mps_task_id=%s", task_id, mps_task_id)

        def report_status(item: MpsTaskResult) -> None:
            if on_external_job is None:
                return
            if item.status in {"SUCCESS", "FINISH", "FINISHED"}:
                status = "succeeded"
            elif item.status in {"FAIL", "FAILED", "ABORTED"}:
                status = "failed"
            else:
                status = "processing"
            on_external_job({
                "external_task_id": mps_task_id,
                "status": status,
                "provider_status": item.provider_status,
                "error_code": item.error_code,
                "error_code_ext": item.error_code_ext,
                "message": item.provider_message,
                "polled": True,
                "completed": status in {"succeeded", "failed"},
            })

        result = mps.wait(mps_task_id, on_progress=progress, on_status=report_status)
        progress(60)

        remote_path = result.subtitle_paths[0]
        suffix = Path(urlparse(remote_path).path).suffix.lower()
        if suffix not in {".vtt", ".srt"}:
            suffix = ".vtt"
        raw_path = download_subtitle(settings, remote_path, task_path / f"subtitle_raw{suffix}")
        text = raw_path.read_text(encoding="utf-8-sig", errors="replace")
        cues = parse_subtitle(text)
        cues = select_language(cues, config.language_mode, config.target_language)
        if config.auto_wrap:
            cues = wrap_cues(cues, config.max_chars_per_line)
        progress(65)
        ass_path = write_ass(task_path / "subtitle.ass", cues, config)
        progress(70)
        output_path, encoder = burn_ass(
            local_input, ass_path, task_path / "final.mp4", ffmpeg_path, ffprobe_path, quality="high"
        )
        progress(90)
        object_path = urlparse(remote_path).path or remote_path
        return SubtitleRunResult(
            output_path=str(output_path), mps_task_id=mps_task_id, subtitle_path=object_path,
            cue_count=len(cues), encoder=encoder,
            metadata={"mps_task_id": mps_task_id, "cos_subtitle_path": object_path,
                      "subtitle_cue_count": len(cues), "ffmpeg_encoder": encoder},
        )
