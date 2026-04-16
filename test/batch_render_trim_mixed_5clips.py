from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


DEFAULT_INPUT_ROOT = Path(r"D:\VideoCut-Wrapper\input\20260416视频素材")
DEFAULT_CONFIG_PATH = Path(r"D:\VideoCut-Wrapper\projects\trim-mixed-5clips\config.json")
DEFAULT_OUTPUT_ROOT = Path(r"D:\VideoCut-Wrapper\output\20260416批量测试")
TEMP_ROOT = Path(r"D:\VideoCut-Wrapper\test\_batch_render_tmp")
WORK_DIR = Path(r"D:\VideoCut-Wrapper")
PYTHON_EXECUTABLE = sys.executable
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="批量调用 trim-mixed-5clips 配置，按 1..5 子目录的同序号视频组合进行渲染。",
    )
    parser.add_argument("--start-index", type=int, default=1, help="从第几个组合开始，默认从 1 开始。")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少组；0 表示全部。")
    parser.add_argument("--dry-run", action="store_true", help="只生成配置和执行计划，不真正渲染。")
    parser.add_argument("--keep-temp", action="store_true", help="保留临时配置目录，方便排查。")
    return parser.parse_args()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def find_slot_videos(input_root: Path) -> dict[int, list[Path]]:
    folders: dict[int, list[Path]] = {}
    for folder_index in range(1, 6):
        folder_dir = input_root / str(folder_index)
        if not folder_dir.is_dir():
            raise FileNotFoundError(f"缺少素材目录: {folder_dir}")
        files = sorted(
            path for path in folder_dir.iterdir()
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
        )
        if len(files) != 5:
            raise ValueError(f"素材目录必须刚好包含 5 个视频: {folder_dir}，当前 {len(files)} 个")
        folders[folder_index] = files
    return folders


def build_case_name(case_index: int) -> str:
    return f"case_{case_index:02d}"


def write_case_config(
    template_config: dict,
    temp_batch_dir: Path,
    case_index: int,
    case_videos: dict[int, Path],
) -> Path:
    case_dir = temp_batch_dir / build_case_name(case_index)
    case_dir.mkdir(parents=True, exist_ok=True)

    config_data = json.loads(json.dumps(template_config))
    variables = config_data.setdefault("variables", {})
    for slot, video_path in case_videos.items():
        variables[f"clip_{slot}"] = video_path.as_posix()

    config_path = case_dir / "config.json"
    config_path.write_text(json.dumps(config_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return config_path


def extract_output_path(stdout: str) -> Path | None:
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Output:"):
            raw = stripped.removeprefix("Output:").strip()
            if raw:
                return Path(raw)
    return None


def decode_output(raw: bytes | None) -> str:
    if not raw:
        return ""
    for encoding in ("utf-8", "gb18030", sys.getdefaultencoding()):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def run_render(python_executable: str, config_path: Path, output_name: str) -> tuple[Path, str]:
    cmd = [python_executable, "-m", "videocut", "render", str(config_path), "--output", output_name]
    result = subprocess.run(
        cmd,
        cwd=str(WORK_DIR),
        capture_output=True,
    )
    stdout = decode_output(result.stdout)
    stderr = decode_output(result.stderr)
    if result.returncode != 0:
        detail = stderr.strip() or stdout.strip() or "未知错误"
        raise RuntimeError(f"渲染失败: {config_path}\n{detail}")

    output_path = extract_output_path(stdout)
    if output_path is None:
        raise RuntimeError(f"未能从命令输出解析渲染结果路径: {config_path}\n{stdout}")
    return output_path, stdout


def main() -> int:
    args = parse_args()
    input_root = DEFAULT_INPUT_ROOT.resolve()
    config_path = DEFAULT_CONFIG_PATH.resolve()
    output_root = DEFAULT_OUTPUT_ROOT.resolve()

    if not config_path.is_file():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    output_root.mkdir(parents=True, exist_ok=True)
    template_config = load_json(config_path)
    folders = find_slot_videos(input_root)
    case_count = len(folders)

    start_index = max(args.start_index, 1)
    end_index = case_count if args.limit <= 0 else min(case_count, start_index + args.limit - 1)
    if start_index > end_index:
        raise ValueError(f"没有可处理的组合: start_index={start_index}, end_index={end_index}")

    batch_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_batch_dir = TEMP_ROOT / f"trim_mixed_5clips_{batch_stamp}"
    temp_batch_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, object] = {
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "inputRoot": str(input_root),
        "baseConfig": str(config_path),
        "outputRoot": str(output_root),
        "dryRun": args.dry_run,
        "cases": [],
    }

    print(f"批量任务准备完成，共 {case_count} 组，实际执行 {start_index}..{end_index}")
    print(f"输入目录: {input_root}")
    print(f"输出目录: {output_root}")
    print(f"基础配置: {config_path}")
    print("")

    try:
        for case_index in range(start_index, end_index + 1):
            case_name = build_case_name(case_index)
            case_videos = {slot: folders[case_index][slot - 1] for slot in range(1, 6)}
            case_config_path = write_case_config(template_config, temp_batch_dir, case_index, case_videos)
            final_output_path = output_root / f"{case_name}.mp4"

            case_record = {
                "case": case_name,
                "config": str(case_config_path),
                "videos": {f"clip_{slot}": str(path) for slot, path in case_videos.items()},
                "finalOutput": str(final_output_path),
                "status": "pending",
            }
            manifest["cases"].append(case_record)

            print(f"[{case_name}]")
            for slot in range(1, 6):
                print(f"  clip_{slot}: {case_videos[slot].name}")

            if args.dry_run:
                case_record["status"] = "dry-run"
                print(f"  dry-run: 已生成配置 {case_config_path}")
                print("")
                continue

            render_output_path, stdout = run_render(PYTHON_EXECUTABLE, case_config_path, f"{case_name}.mp4")
            final_output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(render_output_path, final_output_path)
            case_record["status"] = "completed"
            case_record["renderOutput"] = str(render_output_path)
            case_record["stdout"] = stdout
            print(f"  输出: {final_output_path}")
            print("")
    finally:
        manifest_path = output_root / f"batch_manifest_{batch_stamp}.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not args.keep_temp and temp_batch_dir.exists():
            shutil.rmtree(temp_batch_dir, ignore_errors=True)

    print(f"批量任务结束，清单文件: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
