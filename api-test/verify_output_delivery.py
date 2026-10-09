"""Run inside the deployed container; never calls a generation model.

python /tmp/verify_output_delivery.py --source /path/to/existing.mp4 --evidence-dir /srv/videocut/temp/delivery-check
Use --task-id to verify an existing controlled test task without submitting another render.
Use a task that no downstream consumer will download and delete during this check.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import requests

from videocut.aigc import build_aigc_metadata
from videocut.oss import OssClient


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--task-id")
    args = parser.parse_args()
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    headers = {"X-Api-Key": os.environ["API_KEYS"].split(",")[0].strip()}
    session = requests.Session()
    session.trust_env = False
    oss = OssClient()
    task_id = args.task_id
    input_key = None
    if not task_id:
        if not args.source or not args.source.is_file():
            raise SystemExit("Provide an existing video with --source")
        source = args.evidence_dir / "input.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(args.source), "-t", "3",
                        "-vf", "scale=360:-2", "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", str(source)],
                       check=True, timeout=90)
        input_key = f"{oss.prefix}/diagnostic/output-delivery/{uuid4().hex}/input.mp4"
        oss.upload(source, input_key)
        response = session.post(args.base_url + "/render", headers=headers,
                                json={"pipeline": "bgm-concat", "clips": [input_key], "overrides": {"bgm": {"enabled": False}}}, timeout=30)
        response.raise_for_status()
        task_id = response.json()["taskId"]
        (args.evidence_dir / "task.json").write_text(json.dumps({"taskId": task_id, "inputKey": input_key}), encoding="utf-8")
        print(json.dumps({"submittedTaskId": task_id}), flush=True)
    deadline = time.monotonic() + 180
    while True:
        response = session.get(f"{args.base_url}/tasks/{task_id}", headers=headers, timeout=30)
        response.raise_for_status()
        task = response.json()
        if task["status"] in {"failed", "completed"}:
            break
        if time.monotonic() > deadline:
            raise RuntimeError(f"Task {task_id} did not finish; do not resubmit automatically")
        time.sleep(1)
    assert task["status"] == "completed", task
    assert task["outputUrl"], task
    evidence = task["uploadDiagnostics"]
    remote = oss.head(evidence["ossKey"])
    assert remote["remoteSizeBytes"] == evidence["outputSizeBytes"]
    redirect = session.get(f"{args.base_url}/tasks/{task_id}/download", headers=headers, allow_redirects=False, timeout=30)
    assert redirect.status_code == 302, redirect.text
    # Deliberately do not forward API credentials to OSS.
    download = session.get(redirect.headers["Location"], timeout=60)
    download.raise_for_status()
    assert len(download.content) == remote["remoteSizeBytes"]
    destination = args.evidence_dir / "downloaded.mp4"
    destination.write_bytes(download.content)
    probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(destination)], timeout=30))
    videos = [stream for stream in probe["streams"] if stream["codec_type"] == "video"]
    assert videos, probe
    aigc = json.loads(probe["format"]["tags"]["AIGC"])
    assert aigc == build_aigc_metadata(task_id)
    result = {"taskId": task_id, "inputKey": input_key, "task": task, "remote": remote,
              "downloadStatus": download.status_code, "downloadBytes": len(download.content),
              "downloadSha256": hashlib.sha256(download.content).hexdigest(),
              "videoCodec": videos[0]["codec_name"], "aigc": aigc,
              "localDeleted": not Path(evidence["localOutputPath"]).exists()}
    assert result["localDeleted"], result
    (args.evidence_dir / "evidence.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("taskId", "downloadStatus", "downloadBytes", "downloadSha256", "videoCodec", "localDeleted")}), flush=True)


if __name__ == "__main__":
    main()
