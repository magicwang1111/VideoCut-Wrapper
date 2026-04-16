# VideoCut Wrapper

基于 `Python + FFmpeg` 的模板化视频渲染工具，支持：

- CLI 渲染
- HTTP API 异步渲染
- SQLite 任务持久化
- 阿里云 OSS 或本地 OSS 模式
- 现有 FFmpeg 模板与 `pipeline` 模式

当前仓库已经移除 Node.js/Revideo 运行时，默认只保留 Python 版本。

## 依赖

- Python `>= 3.10`
- `ffmpeg`
- `ffprobe`
- 字体文件放在 `fonts/` 下

如果 `ffmpeg` / `ffprobe` 不在 `PATH`，可以通过环境变量指定：

```powershell
$env:FFMPEG_PATH = 'D:\ffmpeg\bin\ffmpeg.exe'
$env:FFPROBE_PATH = 'D:\ffmpeg\bin\ffprobe.exe'
$env:FFMPEG_ENCODER = 'auto'
```

默认 `FFMPEG_ENCODER=auto`，会优先尝试 `h264_nvenc -> h264_qsv -> h264_amf`，都不可用时自动回退到 `libx264`。

## 安装

```bash
pip install -e .
```

如果只想在仓库内直接运行，也可以不安装脚本入口，直接使用：

```bash
python -m videocut --help
```

## 快速开始

```bash
# 1. 查看可用模板
python -m videocut list

# 2. 初始化项目
python -m videocut init trim-xfade-concat my-project

# 3. 编辑配置
# projects/my-project/config.yaml

# 4. 渲染
python -m videocut render projects/my-project/config.yaml
```

输出文件默认位于 `output/<project-name>/final.mp4`。

## CLI

支持的命令：

- `videocut list`
- `videocut info <template-id>`
- `videocut init <template-id> <project-name>`
- `videocut validate <config>`
- `videocut render <config>`
- `videocut presets`
- `videocut check`
- `videocut serve`

示例：

```bash
videocut render projects/test-trim-xfade/config.yaml --preview
videocut render projects/test-xfade/config.yaml --preset douyin_vertical --quality medium
videocut serve --host 0.0.0.0 --port 3000
```

## 支持的模板

当前只保留 FFmpeg 模板：

- `trim-concat`
- `xfade-concat`
- `trim-xfade-concat`
- `zoom-dissolve-concat`
- `flash-black-concat`
- `trim-mixed-concat`

`simple-slideshow` 和 `product-showcase` 已下线，不再作为可用模板注册。

## 配置文件

普通模板项目示例：

```yaml
template: "trim-xfade-concat"
preset: "auto"
quality: "high"

variables:
  clip_1: "materials/a.mp4"
  clip_2: "materials/b.mp4"
  trim_start: 2
  transition_duration: 0.5
```

素材路径解析顺序：

- 项目目录
- 模板目录
- 仓库根目录

## Pipeline 模式

`pipeline` 模式不依赖模板，适合按配置自由组合素材、裁剪和转场。

```yaml
mode: pipeline
preset: auto
quality: high

clips:
  - src: "D:/input/1.mp4"
    trim_start: 3
    trim_end: 0
  - src: "D:/input/2.mp4"
    trim_start: 2
    trim_end: 1
  - src: "D:/input/3.mp4"
    trim_start: 1

transitions:
  - type: flash-black
    duration: 0.5
  - type: dissolve
    duration: 0.8

default_transition:
  type: cut
  duration: 0
```

运行：

```bash
videocut render projects/pipeline-example/config.yaml
```

## API

启动服务：

```bash
videocut serve --host 0.0.0.0 --port 3000
```

接口：

- `GET /health`
- `POST /upload`
- `POST /render`
- `GET /tasks/{id}`
- `GET /tasks/{id}/download`

鉴权方式：

- 请求头 `X-Api-Key`

上传文件：

```bash
curl -X POST "http://127.0.0.1:3000/upload" \
  -H "X-Api-Key: your-api-key-here" \
  -F "file=@input/1.mp4"
```

提交渲染：

```bash
curl -X POST "http://127.0.0.1:3000/render" \
  -H "X-Api-Key: your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "template": "xfade-concat",
    "clips": ["file_id_1", "file_id_2"],
    "params": {
      "transition_duration": 0.5,
      "preset": "auto",
      "quality": "high"
    }
  }'
```

## 环境变量

参考 `.env.example`：

- `API_KEYS`: 允许访问 API 的 key，逗号分隔
- `FFMPEG_PATH`: 可选，指定 ffmpeg 可执行文件路径
- `FFPROBE_PATH`: 可选，指定 ffprobe 可执行文件路径
- `FFMPEG_ENCODER`: 可选，默认 `auto`，优先探测 GPU 编码器，失败回退到 `libx264`
- `FFMPEG_HWACCEL`: 可选，需要时手动指定 `cuda` 等硬件解码参数
- `OSS_ENDPOINT`
- `OSS_ACCESS_KEY_ID`
- `OSS_ACCESS_KEY_SECRET`
- `OSS_BUCKET`
- `OSS_PREFIX`
- `OSS_LOCAL_ROOT`: 本地 OSS 模式根目录，设置后不会访问阿里云 OSS
- `WORKER_COUNT`
- `QUEUE_MAX`
- `TASK_MAX_ATTEMPT`
- `TASK_TTL_DAYS`
- `DB_PATH`
- `TEMP_DIR`

## 本地 API 测试

如果不想连真实 OSS，可以启用本地 OSS 模式：

```powershell
$env:API_KEYS = 'demo-key'
$env:OSS_LOCAL_ROOT = 'D:\VideoCut-Wrapper\temp\oss-local'
$env:DB_PATH = 'D:\VideoCut-Wrapper\temp\tasks.db'
videocut serve --port 3000
```

这种模式下：

- 上传文件会复制到本地目录
- 输出文件也写到本地目录
- `/tasks/{id}/download` 会直接返回文件

## 仓库结构

```text
videocut/            Python 运行时
templates/           模板 manifest
projects/            示例项目配置
input/               测试素材
output/              渲染输出
temp/                临时目录
fonts/               字体目录
```

## 验证建议

迁移后的最小验证流程：

- `videocut check`
- `videocut list`
- `videocut validate projects/test-xfade/config.yaml`
- `videocut render projects/test-xfade/config.yaml`
- 启动 `videocut serve` 后验证 `upload -> render -> tasks -> download`
