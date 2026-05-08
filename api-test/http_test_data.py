from __future__ import annotations

REAL_OSS_TEST_CLIP_GROUPS: dict[int, list[str]] = {
    1: [
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4390_0.mp4",
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4504_0.mp4",
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4567_0.mp4",
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4662_0.mp4",
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4663_0.mp4",
    ],
    2: [
        "GouMei-Video-Cut/test-input/2/kling_20260328_作品_镜头固定_原地展示穿_4643_0.mp4",
        "GouMei-Video-Cut/test-input/2/kling_20260328_作品_镜头固定_原地展示穿_4659_0.mp4",
        "GouMei-Video-Cut/test-input/2/kling_20260328_作品_镜头固定_原地展示穿_4662_0.mp4",
        "GouMei-Video-Cut/test-input/2/kling_20260328_作品_镜头固定_原地展示穿_4666_0-2.mp4",
        "GouMei-Video-Cut/test-input/2/kling_20260328_作品_镜头固定_原地展示穿_4666_0.mp4",
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4390_0.mp4",
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4504_0.mp4",
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4567_0.mp4",
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4662_0.mp4",
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4663_0.mp4",
    ],
    3: [
        "GouMei-Video-Cut/test-input/3/kling_20260328_作品_往前走_镜头固定_原_4520_0.mp4",
        "GouMei-Video-Cut/test-input/3/kling_20260328_作品_往前走_镜头固定_原_4535_0.mp4",
        "GouMei-Video-Cut/test-input/3/kling_20260328_作品_往前走_镜头固定_原_4538_0.mp4",
        "GouMei-Video-Cut/test-input/3/kling_20260328_作品_往前走_镜头固定_原_4541_0.mp4",
        "GouMei-Video-Cut/test-input/3/kling_20260328_作品_往前走_镜头固定_原_4545_0.mp4",
    ],
    4: [
        "GouMei-Video-Cut/test-input/4/kling_20260328_作品_原地展示穿搭_3338_0.mp4",
        "GouMei-Video-Cut/test-input/4/kling_20260328_作品_镜头固定_原地展示穿_3618_0.mp4",
        "GouMei-Video-Cut/test-input/4/kling_20260328_作品_镜头固定_原地展示穿_3620_0.mp4",
        "GouMei-Video-Cut/test-input/4/kling_20260328_作品_镜头固定_原地展示穿_3625_0.mp4",
        "GouMei-Video-Cut/test-input/4/kling_20260328_作品_镜头固定_原地展示穿_3630_0.mp4",
    ],
    5: [
        "GouMei-Video-Cut/test-input/5/kling_20260327_作品_原地展示穿搭_3512_0.mp4",
        "GouMei-Video-Cut/test-input/5/kling_20260327_作品_原地展示穿搭_3530_0.mp4",
        "GouMei-Video-Cut/test-input/5/kling_20260327_作品_原地展示穿搭_3553_0.mp4",
        "GouMei-Video-Cut/test-input/5/kling_20260327_作品_原地展示穿搭_3605_0.mp4",
        "GouMei-Video-Cut/test-input/5/kling_20260327_作品_原地展示穿搭_3645_0.mp4",
    ],
}


BASE_REAL_OSS_TEST_GROUP_IDS = tuple(sorted(REAL_OSS_TEST_CLIP_GROUPS))
MAX_GENERATED_GROUP_ID = 16


def _take_group_clips(group_id: int, start: int, count: int) -> list[str]:
    clips = REAL_OSS_TEST_CLIP_GROUPS[group_id]
    return [clips[(start + offset) % len(clips)] for offset in range(count)]


def _build_generated_group(group_id: int) -> list[str]:
    base_ids = BASE_REAL_OSS_TEST_GROUP_IDS
    offset = group_id - max(base_ids) - 1
    if group_id <= max(base_ids) + len(base_ids):
        primary = base_ids[offset % len(base_ids)]
        secondary = base_ids[(offset + 1) % len(base_ids)]
        return _take_group_clips(primary, offset, 3) + _take_group_clips(secondary, offset, 2)

    primary = base_ids[offset % len(base_ids)]
    secondary = base_ids[(offset + 2) % len(base_ids)]
    tertiary = base_ids[(offset + 4) % len(base_ids)]
    return (
        _take_group_clips(primary, offset, 2)
        + _take_group_clips(secondary, offset, 2)
        + _take_group_clips(tertiary, offset, 1)
    )


for generated_group_id in range(max(BASE_REAL_OSS_TEST_GROUP_IDS) + 1, MAX_GENERATED_GROUP_ID + 1):
    REAL_OSS_TEST_CLIP_GROUPS[generated_group_id] = _build_generated_group(generated_group_id)


def validate_group_ids(group_ids: list[int]) -> None:
    missing = [group_id for group_id in group_ids if group_id not in REAL_OSS_TEST_CLIP_GROUPS]
    if missing:
        available = sorted(REAL_OSS_TEST_CLIP_GROUPS)
        raise RuntimeError(f"Unsupported group ids: {missing}; available={available}")
