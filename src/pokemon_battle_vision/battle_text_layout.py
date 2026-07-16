"""BATTLE_TEXT 文字遮罩的 layout fingerprint 與穩定變化距離。"""

from dataclasses import asdict, dataclass
from typing import Any, Dict, Sequence

import cv2
import numpy as np


@dataclass(frozen=True)
class BattleTextLayoutConfig:
    hash_width: int = 32
    hash_height: int = 10
    row_bins: int = 10
    column_bins: int = 16
    hash_weight: float = 0.55
    row_weight: float = 0.15
    column_weight: float = 0.15
    bbox_weight: float = 0.10
    component_weight: float = 0.05

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


DEFAULT_BATTLE_TEXT_LAYOUT_CONFIG = BattleTextLayoutConfig()


@dataclass(frozen=True)
class BattleTextLayoutFingerprint:
    layout_hash: str
    row_profile: Sequence[float]
    column_profile: Sequence[float]
    bbox: Sequence[float]
    component_count: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _profile(values: np.ndarray, bins: int) -> Sequence[float]:
    resized = cv2.resize(
        values.astype(np.float32).reshape(1, -1),
        (bins, 1),
        interpolation=cv2.INTER_AREA,
    ).reshape(-1)
    maximum = float(np.max(resized)) if resized.size else 0.0
    if maximum > 1e-9:
        resized = resized / maximum
    return tuple(round(float(value), 4) for value in resized)


def build_layout_fingerprint(
    mask: np.ndarray,
    component_count: int,
    config: BattleTextLayoutConfig = DEFAULT_BATTLE_TEXT_LAYOUT_CONFIG,
) -> BattleTextLayoutFingerprint:
    binary = mask.astype(bool)
    small = cv2.resize(
        binary.astype(np.float32),
        (config.hash_width, config.hash_height),
        interpolation=cv2.INTER_AREA,
    )
    bits = small >= 0.025
    packed = np.packbits(bits.reshape(-1).astype(np.uint8))
    ys, xs = np.nonzero(binary)
    if xs.size:
        height, width = binary.shape
        bbox = (
            float(np.min(xs)) / width,
            float(np.min(ys)) / height,
            float(np.max(xs) + 1) / width,
            float(np.max(ys) + 1) / height,
        )
    else:
        bbox = (0.0, 0.0, 0.0, 0.0)
    return BattleTextLayoutFingerprint(
        layout_hash=packed.tobytes().hex(),
        row_profile=_profile(np.mean(binary, axis=1), config.row_bins),
        column_profile=_profile(np.mean(binary, axis=0), config.column_bins),
        bbox=tuple(round(value, 4) for value in bbox),
        component_count=int(component_count),
    )


def layout_hamming_distance(left: str, right: str) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    left_bytes = bytes.fromhex(left)
    right_bytes = bytes.fromhex(right)
    different = sum(bin(a ^ b).count("1") for a, b in zip(left_bytes, right_bytes))
    return different / float(len(left_bytes) * 8)


def _mean_absolute(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return float(np.mean(np.abs(np.asarray(left, dtype=np.float32) - np.asarray(right, dtype=np.float32))))


def layout_fingerprint_distance(
    left: Dict[str, Any],
    right: Dict[str, Any],
    config: BattleTextLayoutConfig = DEFAULT_BATTLE_TEXT_LAYOUT_CONFIG,
) -> float:
    if not left or not right:
        return 0.0
    hash_distance = layout_hamming_distance(
        str(left.get("layout_hash", "")), str(right.get("layout_hash", ""))
    )
    row_distance = _mean_absolute(
        left.get("row_profile", ()), right.get("row_profile", ())
    )
    column_distance = _mean_absolute(
        left.get("column_profile", ()), right.get("column_profile", ())
    )
    bbox_distance = _mean_absolute(left.get("bbox", ()), right.get("bbox", ()))
    left_count = int(left.get("component_count", 0))
    right_count = int(right.get("component_count", 0))
    count_distance = abs(left_count - right_count) / float(max(1, left_count, right_count))
    distance = (
        config.hash_weight * hash_distance
        + config.row_weight * row_distance
        + config.column_weight * column_distance
        + config.bbox_weight * bbox_distance
        + config.component_weight * count_distance
    )
    return max(0.0, min(1.0, float(distance)))
