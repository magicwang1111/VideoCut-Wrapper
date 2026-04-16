import { makeScene2D, Video, Txt, Audio, type View2D } from '@revideo/2d';
import { waitFor, createRef, useScene } from '@revideo/core';

export default makeScene2D('outro', function* (view: View2D) {
  const vars = useScene().variables;

  const clipSrc = vars.get('clip_outro', '')();
  const outroText = vars.get('outro_text', '关注我们获取更多优惠')();
  const subtitleColor = vars.get('subtitle_color', '#FFFFFF')();
  const duration = vars.get('outro_duration', 3)();
  const enableTransition = vars.get('enable_transition', true)();
  const enableBgm = vars.get('enable_bgm', true)();
  const bgmSrc = vars.get('bgm', '')();
  const bgmVolume = vars.get('bgm_volume', 0.3)();

  // 淡入
  if (enableTransition) {
    view.opacity(0);
    yield* view.opacity(1, 0.5);
  }

  // 主视频层
  const video = createRef<Video>();
  view.add(
    <Video ref={video} src={clipSrc} size={view.size()} play={true} />,
  );

  // 背景音乐（在最后一个场景添加，Revideo 会全局处理音频）
  if (enableBgm && bgmSrc) {
    view.add(<Audio src={bgmSrc} play={true} volume={bgmVolume} />);
  }

  // 结尾文案（底部，淡入）
  const outroLabel = createRef<Txt>();
  view.add(
    <Txt
      ref={outroLabel}
      text={outroText}
      fontSize={48}
      fill={subtitleColor}
      fontFamily="Source Han Sans SC, Microsoft YaHei, sans-serif"
      y={view.height() * 0.35}
      opacity={0}
      shadowColor={'rgba(0,0,0,0.6)'}
      shadowOffset={[2, 2]}
      shadowBlur={4}
    />,
  );

  yield* outroLabel().opacity(1, 0.5);

  // 播放主体
  yield* waitFor(duration - 1);

  // 最终淡出
  yield* view.opacity(0, 0.5);
});
