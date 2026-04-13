import { makeScene2D, Video, Txt, Rect } from '@revideo/2d';
import { waitFor, createRef, useScene } from '@revideo/core';

export default makeScene2D(function* (view) {
  const vars = useScene().variables;

  const clipSrc = vars.get('clip_detail', '')();
  const priceText = vars.get('price_text', '¥99')();
  const duration = vars.get('detail_duration', 5)();
  const enableTransition = vars.get('enable_transition', true)();

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

  // 价格标签（居中，带背景色块）
  const priceContainer = createRef<Rect>();
  const priceLabel = createRef<Txt>();

  view.add(
    <Rect
      ref={priceContainer}
      fill={'rgba(255,255,255,0.9)'}
      padding={[16, 40]}
      radius={12}
      opacity={0}
      y={0}
    >
      <Txt
        ref={priceLabel}
        text={priceText}
        fontSize={96}
        fill={'#FF4444'}
        fontFamily="Source Han Sans SC, Microsoft YaHei, sans-serif"
        fontWeight={700}
      />
    </Rect>,
  );

  // 价格标签弹入动画
  yield* waitFor(0.5);
  priceContainer().y(50);
  yield* priceContainer().opacity(1, 0.3);
  yield* priceContainer().y(0, 0.3);

  // 播放主体
  yield* waitFor(duration - 2);

  // 转场淡出
  if (enableTransition) {
    yield* view.opacity(0, 0.5);
  }
});
