import { makeScene2D, Video } from '@revideo/2d';
import { createRef, useScene, waitFor } from '@revideo/core';

const TRIM_START_SECONDS = 2;
const CLIP_KEYS = [
  'clip_1',
  'clip_2',
  'clip_3',
  'clip_4',
  'clip_5',
  'clip_6',
] as const;

export default makeScene2D(function* (view) {
  const vars = useScene().variables;

  const clips = CLIP_KEYS.map((key) => {
    const src = vars.get(key, '')();
    const sourceDuration = Number(vars.get(`${key}_source_duration`, 0)());
    return {
      src,
      remainingDuration: Math.max(0, sourceDuration - TRIM_START_SECONDS),
    };
  }).filter((clip) => clip.src && clip.remainingDuration > 0);

  for (const clip of clips) {
    const video = createRef<Video>();

    view.add(
      <Video
        ref={video}
        src={clip.src}
        size={view.size()}
        time={TRIM_START_SECONDS}
        play={true}
      />,
    );

    yield* waitFor(clip.remainingDuration);
    video().remove();
  }
});
