# zoom-dissolve-concat 模板设计文档

**日期：** 2026-04-14  
**状态：** 已确认，待实现

---

## 背景

用户需要一个视频拼接模板，实现"拉近+叠化"转场效果：
- 每段出画视频在末尾 0.5s 进行放大（推镜）并淡出至黑
- 下一段视频直接以满屏满亮度切入（不渐入）
- 效果类似剪映的"拉近叠化"转场
- 支持多段视频输入（clip_1 ~ clip_6）

---

## 效果描述

```
clip1：[=====正常播放=====][放大+淡黑0.5s]
clip2：                              [=====正常播放=====][放大+淡黑0.5s]
clip3：                                                        [=====正常播放=====]
output：[===clip1_body===][zoom+fade_1][===clip2_body===][zoom+fade_2][===clip3===]
```

- **每段出画片段**（除最后一段）：末尾 D 秒拆出，放大（1.0x→zoom_scale）+ fade=out 淡黑
- **每段入画片段**：直接硬切，从第1帧满屏亮度播放，无渐入效果
- **总时长** = 所有片段时长之和（无重叠，无时长损耗）

---

## 实现方案：FFmpeg + zoompan + fade

### 为何选 FFmpeg

- 与项目现有 FFmpeg 模板（xfade-concat、trim-xfade-concat）风格一致
- 渲染速度快
- zoompan + fade 滤镜可实现精确的逐帧放大+淡出

### 为何不用 xfade

- xfade 是交叉溶解（两段视频同时参与），clip2 会有淡入效果
- 用户明确需要：只有 clip1 淡出，clip2 直接切入（无淡入）

---

## Filter Complex 逻辑

### 变量

| 变量 | 含义 |
|------|------|
| `D` | transition_duration（秒），默认 0.5 |
| `S` | zoom_scale（倍数），默认 1.2 |
| `FPS` | 视频帧率（从 resPreset 获取） |
| `FRAMES` | `Math.round(FPS * D)`，过渡帧数 |
| `T_i` | clip_i 的 zoom 开始时刻 = `duration_i - D` |

### 每段出画片段的处理（i = 0 到 N-2）

```
[i:v]trim=duration=T_i,setpts=PTS-STARTPTS[body_i];
[i:v]trim=start=T_i,setpts=PTS-STARTPTS[tail_i];
[tail_i]zoompan=z='1+(S-1)*on/max(1,FRAMES-1)':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':fps=FPS:d=FRAMES[zoomed_i];
[zoomed_i]fade=t=out:st=0:d=D[faded_i];
[body_i][faded_i]concat=n=2:v=1:a=0[proc_i];
```

### 最终拼接

```
[proc_0][proc_1]...[proc_{N-2}][N-1:v]concat=n=N:v=1:a=0[vout]
```

### 边缘情况

- **片段时长 < D**：T_i = max(0.01, duration_i - D)，body 接近空，整段都做 zoom+fade
- **单段视频**：直接 `[0:v]copy[vout]`，无效果
- **片段时长 < 2D**：打印警告，但不跳过

---

## 新增文件

### `templates/zoom-dissolve-concat/manifest.json`

```json
{
  "id": "zoom-dissolve-concat",
  "name": "拉近叠化拼接",
  "description": "多段视频拼接，每段出画时推镜放大并淡出，下一段直接切入，转场不含淡入效果",
  "version": "1.0.0",
  "author": "VideoCut Wrapper",
  "entry": "src/project.ts",
  "variables": {
    "clip_1": { "type": "video", "label": "第 1 段视频", "required": true },
    "clip_2": { "type": "video", "label": "第 2 段视频" },
    "clip_3": { "type": "video", "label": "第 3 段视频" },
    "clip_4": { "type": "video", "label": "第 4 段视频" },
    "clip_5": { "type": "video", "label": "第 5 段视频" },
    "clip_6": { "type": "video", "label": "第 6 段视频" },
    "transition_duration": {
      "type": "number",
      "label": "推镜时长（秒）",
      "default": 0.5,
      "min": 0.1,
      "max": 3.0
    },
    "zoom_scale": {
      "type": "number",
      "label": "放大倍数",
      "default": 1.2,
      "min": 1.05,
      "max": 2.0
    }
  },
  "tags": ["拼接", "转场", "拉近", "推镜"]
}
```

### `templates/zoom-dissolve-concat/src/project.ts`

FFmpeg 模板的占位文件（与 xfade-concat 相同）。

---

## 修改文件

### `src/render/index.ts`

**改动点 1：** 第 513 行，在 FFmpeg 模板列表加入新模板：

```typescript
if (['trim-concat', 'xfade-concat', 'trim-xfade-concat', 'zoom-dissolve-concat'].includes(request.templateId)) {
```

**改动点 2：** 第 708 行后，新增路由块：

```typescript
if (request.templateId === 'zoom-dissolve-concat') {
  const transitionDuration = typeof request.variables['transition_duration'] === 'number'
    ? request.variables['transition_duration'] : 0.5;
  const zoomScale = typeof request.variables['zoom_scale'] === 'number'
    ? request.variables['zoom_scale'] : 1.2;

  const clips = collectClips(request.variables, request.templateInfo, videoInfo);
  if (clips.length === 0) throw new RenderError('没有可用的视频片段');

  const outputPath = path.join(outDir, outFile);
  this.ffmpegZoomDissolveConcat(ffmpegPath!, clips, outputPath, qualPreset, task, resPreset, transitionDuration, zoomScale);
  // ...complete task, writeMeta, return...
}
```

**改动点 3：** 新增私有方法 `ffmpegZoomDissolveConcat()`，参数包含 `resPreset`（用于获取 fps）。

---

## 验证方法

1. 创建测试项目目录，config.yaml 指定模板 `zoom-dissolve-concat`，提供 2-3 段测试视频
2. 运行 `videocut render <project_dir>`
3. 检查输出视频：
   - 每段视频末尾有明显放大+淡出效果
   - 下一段视频直接硬切出现（无渐入）
   - 总时长 ≈ 所有输入片段时长之和
4. 用 `videocut list` 确认新模板出现在列表中
