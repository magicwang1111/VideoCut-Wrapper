import { makeScene2D, Video, Img, Txt, type View2D } from '@revideo/2d';
import { waitFor, createRef, useScene } from '@revideo/core';

export default makeScene2D('intro', function* (view: View2D) {
  const vars = useScene().variables;

  const clipSrc = vars.get('clip_intro', '')();
  const productName = vars.get('product_name', '产品名称')();
  const logoSrc = vars.get('logo', '')();
  const subtitleColor = vars.get('subtitle_color', '#FFFFFF')();
  const duration = vars.get('intro_duration', 3)();
  const enableTransition = vars.get('enable_transition', true)();

  // 主视频层
  const video = createRef<Video>();
  view.add(
    <Video ref={video} src={clipSrc} size={view.size()} play={true} />,
  );

  // Logo 层（右上角）
  if (logoSrc) {
    const logo = createRef<Img>();
    view.add(
      <Img
        ref={logo}
        src={logoSrc}
        position={[view.width() / 2 - 80, -view.height() / 2 + 80]}
        width={100}
        opacity={0.8}
      />,
    );
  }

  // 产品名称字幕（底部居中，淡入）
  const title = createRef<Txt>();
  view.add(
    <Txt
      ref={title}
      text={productName}
      fontSize={64}
      fill={subtitleColor}
      fontFamily="Source Han Sans SC, Microsoft YaHei, sans-serif"
      y={view.height() * 0.3}
      opacity={0}
      shadowColor={'rgba(0,0,0,0.6)'}
      shadowOffset={[2, 2]}
      shadowBlur={4}
    />,
  );

  // 字幕淡入
  yield* title().opacity(1, 0.5);

  // 播放主体时长
  yield* waitFor(duration - 1);

  // 转场淡出
  if (enableTransition) {
    yield* view.opacity(0, 0.5);
  }
});
