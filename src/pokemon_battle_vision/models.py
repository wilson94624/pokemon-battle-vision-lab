"""Checkpoint 1A 的小型、可序列化資料模型。"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np


@dataclass(frozen=True)
class VideoProfile:
    profile_id: str
    display_width: int
    display_height: int
    game: str
    battle_format: str
    language: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NormalizedRoi:
    roi_id: str
    x: float
    y: float
    width: float
    height: float
    purpose: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PixelRoi:
    roi_id: str
    x: int
    y: int
    width: int
    height: int

    @property
    def x2(self) -> int:
        return self.x + self.width

    @property
    def y2(self) -> int:
        return self.y + self.height

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FrameTimestampIndex:
    pts_sec: np.ndarray
    duration_sec: np.ndarray
    key_frame: np.ndarray
    validation: Dict[str, Any]
    video_sha256: str
    ffprobe_version: str

    @property
    def frame_count(self) -> int:
        return int(self.pts_sec.size)

    def nearest_ordinal(self, target_sec: float) -> int:
        """以 PTS 二分搜尋最近影格；等距時明確選較早 ordinal。"""
        if self.frame_count == 0:
            raise ValueError("PTS index 為空")
        right = int(np.searchsorted(self.pts_sec, target_sec, side="left"))
        if right <= 0:
            return 0
        if right >= self.frame_count:
            return self.frame_count - 1
        left = right - 1
        left_delta = target_sec - float(self.pts_sec[left])
        right_delta = float(self.pts_sec[right]) - target_sec
        return left if left_delta <= right_delta else right


@dataclass
class AnchorDefinition:
    anchor_id: str
    target_sec: float
    tolerance_sec: float
    state: str
    reference_image: str
    description: str = ""


@dataclass
class SelectedFrame:
    ordinal: int
    pts_sec: float
    image: np.ndarray = field(repr=False)
    target_sec: Optional[float] = None
    motion_score: Optional[float] = None
    sharpness_score: Optional[float] = None
    reference_difference_score: Optional[float] = None
    selection_score: Optional[float] = None


@dataclass
class DecodeAlignmentReport:
    status: str
    ffprobe_frame_count: int
    opencv_decoded_frame_count: int
    first_possible_mismatch_ordinal: Optional[int]
    nearby_pts: List[Dict[str, Any]]
    pts_missing_count: int
    pts_duplicate_count: int
    pts_non_monotonic_count: int
    codec: str
    opencv_backend: str
    ffmpeg_version: str
    ffprobe_version: str
    opencv_version: str
    orientation_auto_disabled: bool
    encoded_dimensions: Dict[str, int]
    first_decoded_dimensions: Optional[Dict[str, int]]
    first_display_dimensions: Optional[Dict[str, int]]
    ordinal_position_mismatches: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
