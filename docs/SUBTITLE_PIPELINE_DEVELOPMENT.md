# VideoCut Wrapper 自动字幕压制 Pipeline 开发设计

## 1. 文档目标

在 `VideoCut-Wrapper` 中新增一个独立的 `subtitle-burn` pipeline，用于完成以下流程：

1. 接收已经存在于阿里云 OSS 的单个视频。
2. 使用 OSS 限时签名 URL 将视频交给腾讯云 MPS 做智能字幕识别。
3. 从腾讯云 COS 下载 MPS 生成的临时字幕文件。
4. 在本地生成带样式的 ASS 字幕。
5. 使用本地 FFmpeg 将 ASS 压制进原视频。
6. 将最终带硬字幕的 MP4 上传到阿里云 OSS。
7. 通过现有任务查询接口返回最终 OSS `outputUrl`。

本文只描述 `VideoCut-Wrapper` 后端的开发范围，不包含前端交互或人工下载流程。

## 2. 核心结论

- 视频输入：阿里云 OSS。
- 最终视频输出：阿里云 OSS。
- 腾讯云 MPS：只负责语音识别和字幕生成，不负责视频压制。
- 腾讯云 COS：只保存 MPS 智能字幕任务产生的中间字幕文件。
- 本地磁盘：只保存任务执行期间的输入视频、原始字幕、ASS 和压制结果。
- FFmpeg：负责本地硬字幕压制。
- API：复用现有 `POST /render`、任务队列和 `GET /tasks/{taskId}`。
- 最终结果：以 `GET /tasks/{taskId}` 返回的 OSS `outputUrl` 为准，不增加预览接口。

目标数据流：

```text
OSS 输入视频
  -> 生成限时签名 GET URL
  -> 腾讯 MPS 智能字幕识别
  -> COS 中间 VTT/SRT
  -> 下载到 worker 临时目录
  -> 解析、选语言、换行、生成 ASS
  -> 本地 FFmpeg 压制
  -> ffprobe 校验
  -> 上传最终 MP4 到 OSS
  -> /tasks/{taskId}.outputUrl
```

## 3. 范围

### 3.1 本期实现

- 一个任务只接受一个视频。
- 输入使用 OSS object key，不接受本地路径或任意 HTTP URL。
- 自动识别源语言，当前固定保留原文，不启用翻译。
- 字幕格式和样式使用 pipeline 内固定配置，不通过 API 开放。
- 输出 H.264/AAC MP4。
- 支持 NVIDIA GPU 编码和 CPU 编码。
- 沿用当前任务队列、重试、任务状态和 OSS 输出 URL 机制。

### 3.2 本期不实现

- 不调用腾讯 MPS 的字幕压制或转码任务。
- 不把 ASS 上传到 OSS。
- 不把最终 MP4 输出到 COS。
- 不提供视频预览。
- 不支持客户端直接上传字幕文件。
- 不支持在一个字幕任务中拼接多个视频。
- 不修改现有拼接、转场和 BGM pipeline 的行为。

## 4. 为什么使用专用 Pipeline 执行路径

当前 `PipelineRunner` 的主要职责是裁剪、规格归一化、拼接、转场和 BGM。单视频拼接路径会输出无音轨视频，随后再按需要混入 BGM。字幕识别依赖原视频中的人声，因此不能简单地把 MPS 识别放在现有拼接输出之后。

本方案为 `subtitle-burn` 增加专用执行路径：

- 识别输入始终是调用方提交的原始 OSS 视频。
- FFmpeg 压制输入始终是 worker 下载到本地的原始视频。
- 原视频音轨在最终输出中保留并转为 AAC。
- 现有普通 pipeline 的渲染行为保持不变。

首期不建议把字幕处理抽象成任意 pipeline 都能启用的通用后处理器。等单视频链路稳定后，再评估将字幕模块复用到拼接结果上。

## 5. Pipeline 配置

新增文件：

```text
pipelines/subtitle-burn/config.json
```

建议内容：

```json
{
  "name": "subtitle-burn",
  "mode": "pipeline",
  "required_clip_count": 1,
  "preset": "auto",
  "quality": "high",
  "clips": [
    {
      "source_index": 0,
      "trim_start": 0,
      "trim_end": 0
    }
  ],
  "default_transition": {
    "type": "cut",
    "duration": 0
  },
  "subtitle": {
    "enabled": true,
    "definition": 122,
    "target_language": "auto",
    "language_mode": "source",
    "accurate_mode": true,
    "need_wordlist": true,
    "adapt_words": "",
    "font_name": "msyh.ttc",
    "font_size": 40,
    "font_color": "#FFFFFF",
    "font_alpha": 0.9,
    "position": "bottom",
    "auto_wrap": true,
    "max_chars_per_line": 10,
    "margin_v": 200,
    "strip_punctuation": true,
    "max_chars_per_cue": 10
  },
  "output": {
    "filename": "final.mp4"
  }
}
```

以上字幕参数是 `subtitle-burn` 的固定业务配置，不通过 API 暴露，也不允许请求覆盖。`overrides` 唯一允许的运行时能力是 `overrides.bgm`。当前字幕固定为：高精度识别、输出词级时间戳、自动识别语言、保留原文、`msyh.ttc`（内部 family 为 `Microsoft YaHei UI`）、40 号白字、0.9 文字透明度、底部向上约 200 像素、去除标点、每个独立字幕段最多 10 个字符、每行最多 10 个字符。

截图中的 `subtitle_format=srt` 用于单独保存字幕文件；本 pipeline 不输出独立字幕文件，因此不设置该字段。MPS 原始 VTT/SRT 只作为中间文件，压制前统一转换为 ASS。

`subtitle.enabled=true` 是专用执行路径的判定条件。配置解析阶段应同时要求：

- `required_clip_count` 必须为 `1`。
- `clips` 必须只有一个元素。
- 不允许配置 `transitions`。
- 首期不允许启用 `bgm`。
- `definition` 必须为正整数。
- 当前发布配置必须保持 `target_language=auto`、`language_mode=source`、`accurate_mode=true`。
- 当前发布配置必须保持 `font_name=msyh.ttc`、`font_size=40`、`font_color=#FFFFFF`、`font_alpha=0.9`。
- 当前发布配置必须保持 `position=bottom`、`margin_v=200`、`strip_punctuation=true`、`auto_wrap=true`、`max_chars_per_cue=10`、`max_chars_per_line=10`。
- `font_alpha` 范围为 `0.0-1.0`。
- `max_chars_per_line` 必须大于 `0`。
- `max_chars_per_cue` 必须大于 `0`；启用 `need_wordlist` 时，使用腾讯词级 `Start/End` 时间戳按此长度生成独立字幕段。
- `margin_v` 使用 ASS 设计画布单位；对当前 720×1280 竖屏，相比旧值 40 向上移动约 190 个输出像素。
- `strip_punctuation` 在词级切段和自动换行前执行，保留小数点及百分号。

## 6. API 约定

### 6.1 创建任务

继续使用现有接口：

```http
POST /render
X-Api-Key: <api-key>
Content-Type: application/json
```

请求示例：

```json
{
  "pipeline": "subtitle-burn",
  "clips": [
    "GouMei-Video-Cut/subtitle-input/20260721/example.mp4"
  ]
}
```

字幕参数不属于 API 契约。`subtitle-burn` 收到 `overrides.bgm` 以外的运行时覆盖时直接返回 `2010`。不传 BGM 时只压字幕并保留原音轨；指定 BGM 时，MPS 仍识别原视频，FFmpeg 在压制字幕的同时以原声 `1.0`、BGM `1.0` 混合两路音频，不做 ducking。

支持的音乐输入为：

- `{"source":"catalog","category":"calm","filename":"1"}`：公开曲库；`source` 可省略。
- `{"source":"bgm-avatar","category":"口播测试","filename":"1"}`：口播专用曲库。
- `{"source":"template","category":"测试1","filename":"生活感"}`：隐藏模板曲库。
- `{"fileId":"audio123def45"}`：用户通过 `/upload` 上传的临时音频。

模板和口播音乐必须显式提供 `source/category/filename`。口播曲库来自 `oss://goumee-coze/GouMei-Video-Cut/bgm-avatar/`，前端通过鉴权接口 `GET /bgm-avatar` 获取清单。

`clips[0]` 是 OSS object key，不是：

- `oss://...` URI；
- OSS HTTP URL；
- 本地绝对路径；
- `/upload` 返回的普通 VideoCut 输入路径，除非它位于允许的字幕输入前缀下。

服务端必须校验 object key：

- 必须以 `OSS_PREFIX + "/" + SUBTITLE_OSS_INPUT_SUBDIR + "/"` 开头；
- 禁止 `..`、反斜杠和空 key；
- 扩展名必须在允许列表中，例如 `.mp4`、`.mov`、`.mkv`、`.webm`；
- object 必须存在；
- 一个请求只能提交一个 clip。

### 6.2 查询任务

继续使用：

```http
GET /tasks/{taskId}
```

成功示例：

```json
{
  "taskId": "t_xxxxxxxxxxxxxxxx",
  "status": "completed",
  "progress": 100,
  "outputUrl": "https://goumee-coze.oss-cn-hangzhou.aliyuncs.com/GouMei-Video-Cut/subtitle-output/20260721/20260721_170000/t_xxxxxxxxxxxxxxxx/final.mp4",
  "taskKind": "pipeline",
  "sourceName": "subtitle-burn"
}
```

API 不返回本地路径，不增加预览字段。中间 COS 字幕地址默认不向客户端暴露。

## 7. 存储设计

所有 OSS 对象共用同一个 bucket 和业务根目录：

```text
oss://goumee-coze/GouMei-Video-Cut/
  subtitle-input/
  subtitle-output/
```

### 7.1 OSS 输入

默认目录：

```text
oss://goumee-coze/GouMei-Video-Cut/subtitle-input/
```

调用方负责先将视频放入该目录，然后把 object key 放进 `clips`。

worker 使用现有 OSS 凭证完成两件事：

1. 下载视频到任务临时目录，供本地 FFmpeg 使用。
2. 为同一个 object key 生成限时 GET URL，供腾讯 MPS 拉取。

签名 URL 有效期默认 86400 秒。腾讯 MPS 必须使用公网 OSS endpoint，不能使用仅阿里云 VPC 可访问的 internal endpoint。

### 7.2 COS 中间字幕

默认目录：

```text
cos://<TENCENT_COS_BUCKET>/subtitle-output/
```

COS 只解决腾讯 MPS `SmartSubtitlesTask` 的标准输出存储要求。它不是最终业务存储。

建议为 `subtitle-output/` 配置 1-3 天生命周期自动删除，避免长期重复存储。

### 7.3 本地临时文件

单个任务目录示例：

```text
TEMP_DIR/<taskId>_attempt<attempt>_worker<workerId>/
  input.mp4
  mps_raw.vtt
  burn.ass
  final.mp4
  subtitle_meta.json
```

任务成功后删除整个临时目录。任务失败时沿用 `KEEP_FAILED_TASK_TEMP`：

- `0`：失败后删除；
- `1`：保留，便于排查字幕文本或 FFmpeg 命令。

### 7.4 OSS 最终输出

默认目录：

```text
oss://goumee-coze/GouMei-Video-Cut/subtitle-output/
```

建议 key：

```text
GouMei-Video-Cut/subtitle-output/<YYYYMMDD>/<YYYYMMDD_HHMMSS>/<taskId>/final.mp4
```

任务只有在以下条件全部满足后才能标记 `completed`：

1. FFmpeg 返回码为 0。
2. 最终文件存在且大小大于 0。
3. ffprobe 能识别出视频流。
4. 最终文件成功上传 OSS。
5. 能生成合法的 HTTPS `outputUrl`。

## 8. 环境变量

在 `.env.example` 增加以下配置，真实密钥只放部署环境的 `.env` 或密钥管理系统：

```dotenv
# ------------------------------------------------------------------------------
# Subtitle pipeline
# ------------------------------------------------------------------------------

SUBTITLE_OSS_INPUT_SUBDIR=subtitle-input
SUBTITLE_OSS_OUTPUT_SUBDIR=subtitle-output
SUBTITLE_OSS_SIGNED_URL_EXPIRES=86400

TENCENTCLOUD_SECRET_ID=
TENCENTCLOUD_SECRET_KEY=
TENCENT_REGION=ap-guangzhou
TENCENT_MPS_HOST=mps.tencentcloudapi.com
TENCENT_MPS_VERSION=2019-06-12
TENCENT_REQUEST_TIMEOUT=600
TENCENT_POLL_INTERVAL=5
TENCENT_MAX_WAIT_SECONDS=3600

TENCENT_COS_BUCKET=goumee-1444407842
TENCENT_COS_OUTPUT_PREFIX=subtitle-output
TENCENT_SUBTITLE_DEFINITION=122
```

继续复用现有配置：

```dotenv
OSS_ENDPOINT=oss-cn-hangzhou.aliyuncs.com
OSS_PUBLIC_ENDPOINT=oss-cn-hangzhou.aliyuncs.com
OSS_ACCESS_KEY_ID=
OSS_ACCESS_KEY_SECRET=
OSS_STS_TOKEN=
OSS_BUCKET=goumee-coze
OSS_PREFIX=GouMei-Video-Cut

FFMPEG_PATH=
FFPROBE_PATH=
FFMPEG_ENCODER=auto
FFMPEG_HWACCEL=
```

注意：

- 不在配置示例、日志、任务 payload 或 metadata 中写入密钥。
- 字幕 pipeline 与现有 VideoCut 功能共用 `OSS_BUCKET=goumee-coze` 和 `OSS_PREFIX=GouMei-Video-Cut`。
- 输入完整前缀由 `OSS_PREFIX/SUBTITLE_OSS_INPUT_SUBDIR` 组成，即 `GouMei-Video-Cut/subtitle-input/`。
- 输出完整前缀由 `OSS_PREFIX/SUBTITLE_OSS_OUTPUT_SUBDIR` 组成，即 `GouMei-Video-Cut/subtitle-output/`。
- 子目录配置只接受单个安全目录名，禁止 `/`、`\\` 和 `..`，避免越过公共业务根目录。

## 9. FFmpeg 配置

### 9.1 GPU 机器

```dotenv
FFMPEG_ENCODER=h264_nvenc
FFMPEG_HWACCEL=cuda
```

建议视频参数：

```text
-c:v h264_nvenc -preset p5 -cq 18 -pix_fmt yuv420p
```

显式配置 `h264_nvenc` 时，如果 GPU、驱动或容器 runtime 不可用，任务应失败并给出明确错误，不要静默切换编码器。

### 9.2 CPU 机器

```dotenv
FFMPEG_ENCODER=libx264
FFMPEG_HWACCEL=
```

建议视频参数：

```text
-c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p
```

### 9.3 自动模式

```dotenv
FFMPEG_ENCODER=auto
FFMPEG_HWACCEL=
```

沿用项目当前探测顺序：

```text
h264_nvenc -> h264_qsv -> h264_amf -> libx264
```

字幕压制要求 FFmpeg 构建包含 `libass`。启动健康检查或任务执行前应验证 `ffmpeg -filters` 中存在 `ass` 或 `subtitles` filter。

## 10. 腾讯 MPS 调用

### 10.1 提交识别任务

调用：

```text
Action: ProcessMedia
Version: 2019-06-12
Service: mps
Host: mps.tencentcloudapi.com
```

请求主体示意：

```json
{
  "InputInfo": {
    "Type": "URL",
    "UrlInputInfo": {
      "Url": "<OSS signed GET URL>"
    }
  },
  "SmartSubtitlesTask": {
    "Definition": 122,
    "UserExtPara": "{\"translate_dst_language\":\"zh\"}"
  },
  "OutputStorage": {
    "Type": "COS",
    "CosOutputStorage": {
      "Bucket": "<TENCENT_COS_BUCKET>",
      "Region": "ap-guangzhou"
    }
  },
  "OutputDir": "/subtitle-output/"
}
```

`UserExtPara` 按需包含：

- `accurate_mode=1`：高精度识别。
- `need_wordlist=1`：输出词级信息。
- `adapt_words`：热词。
- `translate_dst_language`：目标翻译语言；`auto` 时不要传。

### 10.2 查询任务

使用 `DescribeTaskDetail` 按 `TENCENT_POLL_INTERVAL` 轮询：

- `FINISH`、`FINISHED`、`SUCCESS`：成功。
- `FAIL`、`FAILED`、`ABORTED`：失败。
- 超过 `TENCENT_MAX_WAIT_SECONDS`：超时失败。

字幕结果优先从以下字段中提取：

```text
WorkflowTask.SmartSubtitlesTaskResult[].AsrFullTextTask.Output.SubtitlePath
WorkflowTask.SmartSubtitlesTaskResult[].TransTextTask.Output.SubtitlePath
WorkflowTask.SmartSubtitlesTaskResult[].PureSubtitleTransTask.Output.SubtitlePath
WorkflowTask.SmartSubtitlesTaskResult[].OcrFullTextTask.Output.SubtitlePath
```

如果任务结果显示视频没有音频流，应返回明确业务错误，不继续生成空 ASS 或执行 FFmpeg。

## 11. 字幕解析和 ASS 生成

### 11.1 解析要求

- 至少支持 WebVTT 和 SRT 时间轴。
- 兼容 `HH:MM:SS.mmm`、`MM:SS.mmm` 和 SRT 的逗号毫秒。
- 忽略 `WEBVTT` 文件头、序号行和 `NOTE` 块。
- 保留一个 cue 内的多行文本。
- 解析结束后 cue 数必须大于 0，否则任务失败。
- 每个 cue 必须满足 `start >= 0` 且 `end > start`。

### 11.2 语言选择

- `source`：每个 cue 使用第一行。
- `translation`：每个 cue 使用最后一行。
- `bilingual`：保留全部行。
- `auto`：未指定目标语言时使用第一行；指定目标语言时使用最后一行。

### 11.3 自动换行

启用 `need_wordlist` 时，优先读取 `SegmentSet[].Wordlist[]` 的 `Start`、`End` 和 `Word`，去除标点后按 `max_chars_per_cue` 生成独立时间段；词级信息不可用时回退到字幕文件原有时间段。启用 `auto_wrap` 时，再根据 `max_chars_per_line` 对每个文本行换行。ASS 中使用 `\N` 表示换行。

首期沿用字符数换行策略，不做基于字体像素宽度的复杂排版。

### 11.4 ASS 样式

ASS 脚本建议使用：

- `ScriptType: v4.00+`
- `PlayResX: 1920`
- `PlayResY: 1080`
- `BorderStyle: 1`
- 黑色描边 `Outline=2`
- 左右和垂直边距 `40`
- `ScaledBorderAndShadow: yes`

位置到 ASS Alignment 的映射：

| position | Alignment |
|---|---:|
| bottom-left | 1 |
| bottom | 2 |
| bottom-right | 3 |
| middle | 5 |
| top-left | 7 |
| top | 8 |
| top-right | 9 |

本 pipeline 不调用腾讯压制能力，腾讯字幕识别接口也不会下发字体文件。当前仓库在 `fonts/msyh.ttc` 中自带字体集合，Dockerfile 会将其复制到 `/app/fonts`；生成 ASS 时使用本地 ComfyUI 实际回退选中的内部 family 名称 `Microsoft YaHei UI`，本地 FFmpeg 则通过 `fontsdir` 显式加载项目字体目录，避免 Linux 容器产生不同的字体回退。`fonts/simkai.ttf` 仍保留用于显式选择楷体的兼容配置。

## 12. 本地压制命令

命令结构：

```text
ffmpeg -v error -y \
  [硬件解码参数] \
  -i <local-input-video> \
  -map 0:v:0 \
  -map 0:a? \
  -vf "ass=filename='<escaped-ass-filename>'" \
  [视频编码参数] \
  -c:a aac \
  -b:a 192k \
  -movflags +faststart \
  <local-final.mp4>
```

实现要求：

- 使用参数数组调用 `subprocess`，不要拼接 shell 字符串。
- 在 ASS 所在目录执行 FFmpeg，降低 Windows 路径和盘符转义复杂度。
- 正确处理 ASS 文件名中的单引号和冒号。
- 使用 `-map 0:a?`，兼容无音轨视频。
- 捕获 stderr，失败时只保留尾部有限长度，避免日志过大。
- 设置合理超时；建议默认 3600 秒，并允许环境变量覆盖。
- 压制后必须使用 ffprobe 验证至少存在一个视频流。

## 13. Worker 状态机与进度

建议阶段和进度：

| 进度 | 阶段 | 说明 |
|---:|---|---|
| 5 | input_validating | 校验 OSS key 和对象 |
| 10 | input_downloading | 下载源视频到本地 |
| 15 | mps_submitting | 生成签名 URL，提交 MPS |
| 20-55 | mps_processing | 轮询识别任务 |
| 60 | subtitle_downloading | 从 COS 下载原始字幕 |
| 65 | ass_generating | 解析并生成 ASS |
| 70-88 | ffmpeg_burning | 本地字幕压制 |
| 90 | output_validating | ffprobe 校验 |
| 95 | output_uploading | 上传最终 MP4 到 OSS |
| 100 | completed | 保存 oss_key 和完成任务 |

轮询期间的进度只能单调增加，不能在每次查询时重置。

## 14. 代码组织建议

新增：

```text
pipelines/subtitle-burn/config.json

videocut/subtitle/
  __init__.py
  config.py          # 环境变量和运行配置
  models.py          # subtitle 配置和任务结果类型
  tc3.py             # 腾讯云 TC3-HMAC-SHA256 签名
  mps.py             # ProcessMedia / DescribeTaskDetail
  cos.py             # COS URL 签名和字幕下载
  parser.py          # VTT/SRT cue 解析及语言选择
  ass.py             # ASS 样式和文本生成
  burn.py            # FFmpeg 压制与 ffprobe 校验
  runner.py          # 完整 subtitle-burn 编排
```

修改：

```text
videocut/pipeline/types.py
  - 增加 PipelineSubtitleConfig
  - PipelineConfig 增加 subtitle 字段

videocut/pipeline/config.py
  - 解析 subtitle 配置
  - subtitle-burn 只允许 `overrides.bgm`

videocut/oss/client.py
  - 在现有 OSS_PREFIX 下增加 subtitle_input_key 校验
  - 增加 subtitle_output_key
  - 增加 signed_get_url
  - 不改变现有 input_key/output_key 行为

videocut/api/app.py
  - subtitle-burn 校验 OSS_PREFIX/subtitle-input 前缀
  - 保持其它 pipeline 的 clips 校验不变

videocut/queue/worker_process.py
  - 将原始 OSS key 传给字幕 runner
  - 识别 subtitle.enabled 并走专用执行路径
  - 成功后仍发送 task_rendered

videocut/queue/task_queue.py
  - subtitle-burn 使用 subtitle_output_key
  - 普通 pipeline 继续使用现有 output_key

.env.example
requirements.txt
pyproject.toml
README.md
docs/API.md
test/test_pipeline_api.py
```

依赖策略：

- OSS 继续使用项目已有 `oss2`。
- MPS 可以使用项目内轻量 TC3 请求实现，减少大体积 SDK 依赖。
- COS 签名也可以使用轻量实现；如果选择腾讯云 COS SDK，必须同时更新 `requirements.txt` 和 `pyproject.toml`。
- HTTP 请求若使用 `requests`，必须将其加入两个依赖文件；也可以使用标准库实现，避免新增依赖。

## 15. 任务编排伪代码

```python
def run_subtitle_pipeline(task, source_oss_key, subtitle_config):
    validate_source_key(source_oss_key)
    ensure_source_exists(source_oss_key)

    local_input = download_source_to_task_dir(source_oss_key)
    probe = probe_input(local_input)
    if not probe.has_video:
        raise SubtitleInputError("input has no video stream")
    if not probe.has_audio:
        raise SubtitleInputError("input has no audio stream")

    signed_input_url = oss.signed_get_url(
        source_oss_key,
        expires=subtitle_settings.oss_signed_url_expires,
    )

    mps_task_id = mps.submit_smart_subtitle(
        input_url=signed_input_url,
        definition=subtitle_config.definition,
        target_language=subtitle_config.target_language,
        output_storage=subtitle_settings.cos_output_storage,
    )
    mps_result = mps.wait(mps_task_id)

    raw_subtitle_path = cos.download_subtitle(mps_result.subtitle_path)
    cues = parse_subtitle(raw_subtitle_path)
    cues = select_language(cues, subtitle_config.language_mode)
    cues = wrap_cues(cues, subtitle_config.max_chars_per_line)
    if not cues:
        raise SubtitleResultError("MPS returned no usable subtitle cues")

    ass_path = write_ass(cues, subtitle_config)
    local_output = burn_ass(local_input, ass_path, ffmpeg_settings)
    validate_video(local_output)

    output_key = oss.subtitle_output_key(task.id)
    oss.upload(local_output, output_key)
    return output_key
```

日志和 metadata 中只记录 `source_oss_key`、`mps_task_id`、中间 COS object path、cue 数、编码器和最终 `output_key`，不能记录签名 URL或密钥。

## 16. 错误处理

建议新增业务错误码：

| HTTP/任务状态 | error_code | 场景 |
|---|---:|---|
| 400 | 2009 | subtitle-burn 不是单视频输入 |
| 400 | 2010 | subtitle override 不合法 |
| 400 | 2011 | OSS key 不在字幕输入前缀 |
| failed | 3010 | 输入视频无音轨 |
| failed | 3011 | MPS 提交失败 |
| failed | 3012 | MPS 任务失败或超时 |
| failed | 3013 | 未获得有效字幕地址 |
| failed | 3014 | 字幕解析后 cue 数为 0 |
| failed | 3015 | FFmpeg 字幕压制失败 |
| failed | 3016 | 最终视频校验失败 |
| failed | 3017 | 最终 OSS 上传失败 |

异步 worker 失败仍通过现有任务字段返回：

```json
{
  "status": "failed",
  "lastError": "...",
  "lastErrorAt": "..."
}
```

错误信息必须指出失败阶段，但不能包含 Secret、Authorization header 或完整签名 URL。

## 17. 重试与幂等

- API 创建的 `taskId` 是整个处理链路的幂等标识。
- 每次 worker 重试可以重新创建本地 attempt 目录。
- MPS 提交成功后，应立即把 `mps_task_id` 写入可恢复的任务 metadata，避免 worker 重启后重复提交。
- 同一事务把 `mps_task_id` 写入 `variables.subtitle_state`，并把腾讯任务写入 `task_external_jobs`；前者用于兼容旧镜像恢复，后者用于状态跟踪和 API 查询。
- 如果任务恢复时已有未完成的 `mps_task_id`，优先继续查询原任务。
- 最终 OSS key 由 `taskId` 决定，同一任务重试覆盖同一个 `final.mp4`，不产生多个业务结果。
- 上传完成后再更新任务为 `completed`。
- 如果任务记录已经是 `completed` 且 OSS 对象存在，不重新执行识别或压制。

`task_external_jobs` 使用 `unknown`、`submitted`、`processing`、`succeeded`、`failed` 五种归一化状态，同时保留腾讯原始状态、错误码、扩展错误码和错误消息。每次 MPS 查询更新 `last_polled_at`，成功或失败时写入 `completed_at`。历史 `variables.subtitle_state.mps_task_id` 在启动迁移时幂等回填，无法可靠还原的状态标记为 `unknown`。

任务清理必须先删除对应 `task_external_jobs`。数据库只保存结构化诊断字段，不保存腾讯完整响应、OSS 签名 URL、Secret 或 Authorization header。

## 18. 安全要求

- 所有 Secret 只能通过环境变量或部署密钥系统注入。
- 不提交真实 `.env`。
- OSS 输入签名 URL 使用最小有效期，默认 24 小时。
- 签名 URL 只用于 MPS 请求，不写日志、不写任务数据库、不返回客户端。
- OSS RAM 身份只授予字幕输入目录读权限、字幕输出目录写权限。
- 腾讯云身份只授予 MPS 调用和目标 COS 前缀所需权限。
- 对用户可覆盖的字体名、颜色和数字字段做白名单/类型校验。
- FFmpeg 使用参数数组，禁止把客户端字符串拼进 shell 命令。
- ASS 文本中的 `{` 和 `}` 应转义或替换，避免用户文本被解析成 ASS override tag。
- 最终 `outputUrl` 必须为 HTTPS，不能使用 internal endpoint，也不能编码路径分隔符为 `%2F`。

## 19. 可观测性

每个阶段使用结构化日志，至少包含：

```text
task_id
pipeline
stage
attempt
worker_id
elapsed_seconds
source_oss_key
mps_task_id
subtitle_cue_count
ffmpeg_encoder
output_oss_key
error_type
```

推荐阶段名：

```text
subtitle_config_loaded
subtitle_input_ready
mps_submitted
mps_status
mps_finished
subtitle_downloaded
ass_written
ffmpeg_started
ffmpeg_finished
output_uploaded
done
failed
```

不得记录：

- OSS AccessKey；
- 腾讯云 Secret；
- Authorization header；
- 完整 OSS/COS 签名 URL。

## 20. 测试计划

### 20.1 单元测试

- subtitle pipeline 配置解析和默认值。
- 非单视频配置被拒绝。
- subtitle-burn 对 BGM 以外 runtime override 的拒绝校验，以及三种合法音乐来源校验。
- VTT/SRT 时间轴解析。
- `source`、`translation`、`bilingual`、`auto` 语言选择。
- 中文、英文、双语自动换行。
- ASS 颜色、文字透明度和 Alignment 映射。
- ASS 文本花括号转义。
- OSS 字幕输入前缀校验。
- 字幕输出 key 使用北京时间和固定 taskId。
- MPS 状态归一化和字幕路径提取。
- MPS 无音轨错误识别。
- FFmpeg encoder 选择和参数生成。

### 20.2 集成测试

- 使用 mock MPS/COS，完整执行 OSS 下载到 OSS 上传。
- MPS 提交失败。
- MPS 轮询超时。
- MPS 成功但没有字幕 URL。
- 字幕文件为空或无法解析。
- FFmpeg 返回非 0。
- ffprobe 未发现视频流。
- OSS 输出上传失败时任务不能变成 completed。
- 失败任务临时目录保留开关。
- 普通 pipeline 行为不受影响。

### 20.3 真实环境验收

至少准备以下视频：

1. 中文人声视频。
2. 英文人声并翻译为中文的视频。
3. 双语输出视频。
4. 无音轨视频。
5. 横屏视频。
6. 竖屏视频。
7. 超过 10 分钟的视频。

真实验收需确认：

- MPS 可以读取 OSS 签名 URL。
- COS 能生成原始字幕。
- cue 数大于 0。
- 字幕样式和位置正确。
- 原始音频在最终 MP4 中存在。
- GPU 模式实际使用 `h264_nvenc`。
- CPU 模式实际使用 `libx264`。
- 最终文件位于指定 OSS 输出目录。
- `/tasks/{taskId}` 返回可访问的 `outputUrl`。
- COS 中间字幕按生命周期自动清理。

## 21. 完成标准

以下条件全部满足才视为开发完成：

- `videocut pipelines` 能列出 `subtitle-burn`。
- `POST /render` 能使用一个字幕输入 OSS key 创建异步任务。
- 腾讯 MPS 只执行智能字幕识别，不执行字幕压制或视频转码。
- 本地 FFmpeg 成功生成带硬字幕、带原音轨的 MP4。
- 最终 MP4 上传到 `GouMei-Video-Cut/subtitle-output/`。
- 任务完成响应返回最终 OSS `outputUrl`。
- 无音轨、空字幕、MPS 超时、FFmpeg 失败和 OSS 上传失败都有明确错误。
- GPU 和 CPU 两种配置至少各验证一次。
- 新增单元测试和集成测试通过。
- 现有 pipeline 测试全部通过。
- 文档、`.env.example`、`requirements.txt` 和 `pyproject.toml` 与实现一致。

## 22. 推荐开发顺序

1. 增加 subtitle 配置模型、解析和校验测试。
2. 为 `OssClient` 增加字幕输入签名和字幕输出 key，保持旧方法不变。
3. 移植并测试腾讯 TC3、MPS 提交、查询和结果提取逻辑。
4. 实现 COS 中间字幕下载。
5. 实现 VTT/SRT 解析、语言选择和 ASS 生成。
6. 实现 FFmpeg 压制和 ffprobe 校验。
7. 实现 `SubtitlePipelineRunner` 编排。
8. 接入 worker、任务进度和最终 OSS 上传。
9. 增加 `/render` 对字幕 OSS 前缀的专用校验。
10. 完成 mock 集成测试。
11. 在 GPU 环境跑真实中文、翻译和双语任务。
12. 在 CPU 配置下跑一条真实任务。
13. 补齐 API、部署和运维文档。
