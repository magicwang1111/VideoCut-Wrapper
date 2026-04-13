import { makeScene2D, Video, Rect } from '@revideo/2d';
import { all, waitFor, createRef, useScene } from '@revideo/core';

export default makeScene2D(function* (view) {
  const vars = useScene().variables;

  // 读取变量
  const clip1Src = vars.get('clip_1', '')();
  const clip2Src = vars.get('clip_2', '')();
  const clip3Src = vars.get('clip_3', '')();
  const clip1Duration = vars.get('clip_1_duration', 0)();
  const clip2Duration = vars.get('clip_2_duration', 0)();
  const clip3Duration = vars.get('clip_3_duration', 0)();
  const transitionDuration = vars.get('transition_duration', 0.5)();
  const enableTransition = vars.get('enable_transition', true)();

  // 收集有效片段
  const clips = [
    { src: clip1Src, duration: clip1Duration },
    { src: clip2Src, duration: clip2Duration },
  ];
  if (clip3Src) {
    clips.push({ src: clip3Src, duration: clip3Duration });
  }

  // 创建一个覆盖整个画布的容器
  const container = createRef<Rect>();
  view.add(<Rect ref={container} size={view.size()} />);

  for (let i = 0; i < clips.length; i++) {
    const clip = clips[i];
    const isLast = i === clips.length - 1;

    // 创建视频元素
    const video = createRef<Video>();
    container().add(
      <Video
        ref={video}
        src={clip.src}
        size={view.size()}
        play={true}
        opacity={0}
      />,
    );

    // 淡入（转场效果）
    if (enableTransition && i > 0) {
      yield* video().opacity(1, transitionDuration);
    } else {
      video().opacity(1);
    }

    // 等待播放
    const playDuration = clip.duration > 0 ? clip.duration : 3;
    yield* waitFor(playDuration);

    // 淡出（转场效果，最后一个片段除外）
    if (enableTransition && !isLast) {
      yield* video().opacity(0, transitionDuration);
    }

    // 清理已播放的视频
    video().remove();
  }
});
