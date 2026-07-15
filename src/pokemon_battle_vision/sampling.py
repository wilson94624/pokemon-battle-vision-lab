"""只使用權威 PTS index 的固定時間間隔與最近影格查詢。"""

from typing import List

from .models import FrameTimestampIndex


def fixed_interval_targets(first_pts: float, last_pts: float, interval_sec: float) -> List[float]:
    if interval_sec <= 0:
        raise ValueError("interval_sec 必須大於 0")
    if last_pts < first_pts:
        raise ValueError("last_pts 不可早於 first_pts")
    targets = []
    index = 0
    epsilon = 1e-9
    while True:
        target = first_pts + index * interval_sec
        if target > last_pts + epsilon:
            break
        targets.append(float(target))
        index += 1
    return targets


def fixed_interval_ordinals(
    index: FrameTimestampIndex, interval_sec: float
) -> List[int]:
    if index.frame_count == 0:
        return []
    targets = fixed_interval_targets(
        float(index.pts_sec[0]), float(index.pts_sec[-1]), interval_sec
    )
    ordinals = []
    for target in targets:
        ordinal = index.nearest_ordinal(target)
        if not ordinals or ordinal != ordinals[-1]:
            ordinals.append(ordinal)
    return ordinals

