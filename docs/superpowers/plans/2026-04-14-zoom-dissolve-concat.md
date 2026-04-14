# zoom-dissolve-concat 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建 `zoom-dissolve-concat` FFmpeg 模板，实现"每段出画视频末尾推镜放大+淡黑，下一段直接硬切"的拼接效果。

**Architecture:** 在 `templates/zoom-dissolve-concat/` 新建模板文件，在 `src/render/index.ts` 新增路由分支和私有方法 `ffmpegZoomDissolveConcat()`，使用 FFmpeg `zoompan` + `fade=out` + `concat` 滤镜组合。系统自动发现新模板（无需修改 registry）。

**Tech Stack:** TypeScript, Node.js, FFmpeg (`zoompan` / `fade` / `concat` filters), 已有 `collectClips()` / `getQualityPreset()` 等工具函数。

---

## 文件变更清单

| 操作 | 路径 | 职责 |
|------|------|------|
| 新建 | `templates/zoom-dissolve-concat/manifest.json` | 模板元信息和变量声明 |
| 新建 | `templates/zoom-dissolve-concat/src/project.ts` | FFmpeg 模板占位入口 |
| 修改 | `src/render/index.ts` 第 513 行 | 将新模板加入 FFmpeg 检查列表 |
| 修改 | `src/render/index.ts` 第 708 行后 | 新增路由分支 |
| 修改 | `src/render/index.ts` 类内部 | 新增 `ffmpegZoomDissolveConcat()` 私有方法 |

---

## Task 1：新建模板文件

**Files:**
- Create: `templates/zoom-dissolve-concat/manifest.json`
- Create: `templates/zoom-dissolve-concat/src/project.ts`

- [ ] **Step 1: 创建目录结构**

```bash
mkdir -p D:/Goumei-Video-Cut/templates/zoom-dissolve-concat/src
```

- [ ] **Step 2: 写 manifest.json**

创建 `templates/zoom-dissolve-concat/manifest.json`，内容如下：

```json
{
  "id": "zoom-dissolve-concat",
  "name": "拉近叠化拼接",
  "description": "多段视频拼接，每段出画时推镜放大并淡出至黑，下一段直接硬切进入，无淡入效果",
  "version": "1.0.0",
  "author": "Goumei",
  "entry": "src/project.ts",
  "variables": {
    "clip_1": {
      "type": "video",
      "label": "第 1 段视频",
      "required": true
    },
    "clip_2": {
      "type": "video",
      "label": "第 2 段视频"
    },
    "clip_3": {
      "type": "video",
      "label": "第 3 段视频"
    },
    "clip_4": {
      "type": "video",
      "label": "第 4 段视频"
    },
    "clip_5": {
      "type": "video",
      "label": "第 5 段视频"
    },
    "clip_6": {
      "type": "video",
      "label": "第 6 段视频"
    },
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

- [ ] **Step 3: 写占位 project.ts**

创建 `templates/zoom-dissolve-concat/src/project.ts`，内容如下：

```typescript
// zoom-dissolve-concat 模板由 FFmpeg 直接渲染，此文件仅作为 registry entry 占位
export default {};
```

- [ ] **Step 4: 验证模板被发现**

```bash
cd D:/Goumei-Video-Cut && node --loader ts-node/esm src/cli.ts list
```

期望输出：列表中出现 `zoom-dissolve-concat` 条目（如果 CLI 支持 `list` 命令）。

- [ ] **Step 5: Commit**

```bash
cd D:/Goumei-Video-Cut
git add templates/zoom-dissolve-concat/
git commit -m "feat: add zoom-dissolve-concat template manifest and entry"
```

---

## Task 2：在 render/index.ts 添加 FFmpeg 检查和路由

**Files:**
- Modify: `src/render/index.ts:513` — FFmpeg 模板列表
- Modify: `src/render/index.ts:708` — 新增路由分支

- [ ] **Step 1: 将新模板加入 FFmpeg 依赖检查**

在 `src/render/index.ts` 第 513 行，修改 FFmpeg 检查列表：

```typescript
// 修改前
if (['trim-concat', 'xfade-concat', 'trim-xfade-concat'].includes(request.templateId)) {

// 修改后
if (['trim-concat', 'xfade-concat', 'trim-xfade-concat', 'zoom-dissolve-concat'].includes(request.templateId)) {
```

- [ ] **Step 2: 新增路由分支**

在第 708 行（`trim-xfade-concat` 路由块的最后一个 `}` 之后，`// ── 其他模板` 注释之前）插入：

```typescript
      // ── zoom-dissolve-concat：推镜放大+淡黑，直接拼接 ──
      if (request.templateId === 'zoom-dissolve-concat') {
        console.log(`\n[1/3] 收集并校验视频片段...`);

        const transitionDuration = typeof request.variables['transition_duration'] === 'number'
          ? request.variables['transition_duration']
          : 0.5;
        const zoomScale = typeof request.variables['zoom_scale'] === 'number'
          ? request.variables['zoom_scale']
          : 1.2;

        const clips = collectClips(request.variables, request.templateInfo, videoInfo);

        if (clips.length === 0) {
          throw new RenderError('没有可用的视频片段（未提供任何视频）');
        }

        console.log(`  共 ${clips.length} 个片段，推镜时长 ${transitionDuration}s，放大倍数 ${zoomScale}x`);

        const outputPath = path.join(outDir, outFile);
        this.ffmpegZoomDissolveConcat(
          ffmpegPath!,
          clips,
          outputPath,
          qualPreset,
          task,
          resPreset,
          transitionDuration,
          zoomScale,
        );

        const elapsed = (Date.now() - startTime) / 1000;
        completeTask(task, outputPath);

        console.log(`[3/3] 归档产物...`);
        await this.writeMeta(outDir, task, request, resPreset, elapsed);

        return {
          taskId: task.id,
          status: 'completed',
          outputPath,
          duration: elapsed,
        };
      }
```

- [ ] **Step 3: Commit**

```bash
cd D:/Goumei-Video-Cut
git add src/render/index.ts
git commit -m "feat: add zoom-dissolve-concat routing in render/index.ts"
```

---

## Task 3：实现 ffmpegZoomDissolveConcat() 方法

**Files:**
- Modify: `src/render/index.ts` — 在 `ffmpegXfadeConcat()` 方法（第 494 行）之后插入新方法

**背景知识：**
- `zoompan` filter 参数：`z`=缩放表达式，`x/y`=裁切偏移（居中），`fps`=帧率，`d`=帧数
- `on` 是 zoompan 内置变量，表示当前输出帧序号（从 0 开始）
- `zoom` 是 zoompan 内置变量，表示当前累计缩放值（但我们用 `on` 直接线性计算更可靠）
- `fade=t=out:st=0:d=D` 从第 0 秒开始，用 D 秒淡出至黑
- `concat=n=N:v=1:a=0` 将 N 段视频流拼接（只处理视频，忽略音频）

- [ ] **Step 1: 在 ffmpegXfadeConcat() 末尾（第 494 行 `}`）之后插入新方法**

```typescript
  /**
   * 推镜叠化拼接：每段出画片段的最后 D 秒做 zoompan 放大 + fade=out，
   * 然后直接 concat 下一段（无交叉叠化，入画片段硬切，无淡入）。
   */
  private ffmpegZoomDissolveConcat(
    ffmpegPath: string,
    clips: Array<{ key: string; src: string; duration: number }>,
    outputPath: string,
    qualPreset: QualityPreset,
    task: RenderTask,
    resPreset: ResolutionPreset,
    transitionDuration: number,
    zoomScale: number,
  ): void {
    const n = clips.length;
    const D = transitionDuration;
    const fps = resPreset.fps;
    const frames = Math.max(1, Math.round(fps * D)); // 过渡帧数

    // 缩放增量表达式：从 1.0 线性增长到 zoomScale，共 frames 帧
    // on=0 时 z=1.0，on=frames-1 时 z=zoomScale
    const denominator = Math.max(1, frames - 1);
    const zExpr = `1+(${(zoomScale - 1).toFixed(6)})*on/${denominator}`;
    const xExpr = `(iw-iw/zoom)/2`;
    const yExpr = `(ih-ih/zoom)/2`;

    // ── 构造输入参数 ──────────────────────────────────────────────────────────
    const inputArgs: string[] = [];
    for (const clip of clips) {
      inputArgs.push('-i', clip.src);
    }

    // ── 构建 filter_complex ───────────────────────────────────────────────────
    const filterParts: string[] = [];

    if (n === 1) {
      filterParts.push(`[0:v]copy[vout]`);
    } else {
      // 对每段出画片段（索引 0 到 n-2）拆分：正常 body + 推镜 tail
      for (let i = 0; i < n - 1; i++) {
        const dur = clips[i].duration;
        const tailStart = Math.max(0.001, dur - D);
        const tailStartStr = tailStart.toFixed(6);

        // body: 0 到 tailStart
        filterParts.push(
          `[${i}:v]trim=duration=${tailStartStr},setpts=PTS-STARTPTS[body${i}]`,
        );
        // tail: tailStart 到结尾
        filterParts.push(
          `[${i}:v]trim=start=${tailStartStr},setpts=PTS-STARTPTS[tail${i}]`,
        );
        // zoompan 放大
        filterParts.push(
          `[tail${i}]zoompan=z='${zExpr}':x='${xExpr}':y='${yExpr}':fps=${fps}:d=${frames}[zoomed${i}]`,
        );
        // fade=out 淡至黑
        filterParts.push(
          `[zoomed${i}]fade=t=out:st=0:d=${D}[faded${i}]`,
        );
        // 合并 body + faded tail
        filterParts.push(
          `[body${i}][faded${i}]concat=n=2:v=1:a=0[proc${i}]`,
        );

        if (dur < D) {
          console.warn(
            `  ⚠ 片段 ${clips[i].key} 时长 ${dur.toFixed(1)}s 短于推镜时长（${D}s），效果可能异常`,
          );
        }
      }

      // 拼接所有处理后的片段 + 最后一段（不做推镜）
      const concatInputs = [
        ...Array.from({ length: n - 1 }, (_, i) => `[proc${i}]`),
        `[${n - 1}:v]`,
      ].join('');
      filterParts.push(
        `${concatInputs}concat=n=${n}:v=1:a=0[vout]`,
      );
    }

    const filterComplex = filterParts.join(';');

    const args: string[] = [
      ...inputArgs,
      '-filter_complex', filterComplex,
      '-map', '[vout]',
      '-c:v', 'libx264',
      '-preset', qualPreset.ffmpegPreset,
      '-crf', String(qualPreset.crf),
      '-pix_fmt', 'yuv420p',
      '-an',
      '-y', outputPath,
    ];

    console.log(`\n[2/3] FFmpeg 推镜拼接 ${n} 个片段（推镜 ${D}s，放大 ${zoomScale}x）...`);
    execFileSync(ffmpegPath, args, { timeout: 600_000 });

    updateProgress(task, 1);
  }
```

- [ ] **Step 2: 确认 TypeScript 编译无报错**

```bash
cd D:/Goumei-Video-Cut && npx tsc --noEmit
```

期望：无任何 error 输出。

- [ ] **Step 3: Commit**

```bash
cd D:/Goumei-Video-Cut
git add src/render/index.ts
git commit -m "feat: implement ffmpegZoomDissolveConcat with zoompan+fade+concat"
```

---

## Task 4：端到端验证

**Files:**
- Create: `projects/test-zoom-dissolve/config.yaml`（测试用，不提交）

- [ ] **Step 1: 创建测试项目配置**

创建 `projects/test-zoom-dissolve/config.yaml`，使用你本地已有的测试视频路径：

```yaml
template: zoom-dissolve-concat
preset: auto
quality: high
variables:
  clip_1: "D:/path/to/your/test_video_1.mp4"
  clip_2: "D:/path/to/your/test_video_2.mp4"
  clip_3: "D:/path/to/your/test_video_3.mp4"
  transition_duration: 0.5
  zoom_scale: 1.2
```

> ⚠ 将路径替换为实际存在的视频文件

- [ ] **Step 2: 执行渲染**

```bash
cd D:/Goumei-Video-Cut && node --loader ts-node/esm src/cli.ts render projects/test-zoom-dissolve
```

期望：
- 无报错
- 输出 `output/test-zoom-dissolve/final.mp4`
- 控制台显示 `FFmpeg 推镜拼接 3 个片段（推镜 0.5s，放大 1.2x）...`

- [ ] **Step 3: 检查输出视频时长**

```bash
ffprobe -v quiet -show_entries format=duration -of csv=p=0 output/test-zoom-dissolve/final.mp4
```

期望：输出时长 ≈ clip_1时长 + clip_2时长 + clip_3时长（误差 < 0.1s，无重叠损耗）

- [ ] **Step 4: 肉眼确认效果**

用播放器打开 `output/test-zoom-dissolve/final.mp4`，确认：
1. 每段视频末尾有明显放大 + 淡黑效果（约 0.5s）
2. 下一段视频直接满屏切入，无渐入
3. 总体流畅，无花屏/黑帧异常

- [ ] **Step 5: 验证模板在列表中**

```bash
cd D:/Goumei-Video-Cut && node --loader ts-node/esm src/cli.ts list
```

期望：`zoom-dissolve-concat` 出现在模板列表中，显示名称"拉近叠化拼接"

---

## 自查结果

- [x] 设计文档中所有需求均有对应 Task
- [x] 无 TBD / TODO 占位
- [x] 方法签名一致：`ffmpegZoomDissolveConcat()` 在 Task 2 路由中调用，在 Task 3 中定义，参数一致
- [x] `ResolutionPreset` 类型已在文件顶部导入（现有代码已引入）
- [x] 过渡帧数 `frames` 与 `fade=t=out:d=${D}` 都使用同一 `D` 值
