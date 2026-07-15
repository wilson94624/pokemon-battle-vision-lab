"""Checkpoint 1A 可對應到 CLI exit code 的領域錯誤。"""


class CheckpointError(Exception):
    """所有預期中、可向使用者解釋的流程錯誤。"""

    exit_code = 1


class InputError(CheckpointError):
    """輸入檔不存在、不可讀或 schema 不正確。"""

    exit_code = 2


class DependencyError(CheckpointError):
    """FFmpeg／ffprobe 不存在或 capability probe 失敗。"""

    exit_code = 3


class MediaProbeError(CheckpointError):
    """ffprobe 執行、JSON 或必要欄位錯誤。"""

    exit_code = 4


class CompatibilityError(CheckpointError):
    """影片 profile 與唯一支援規格不相容。"""

    exit_code = 5


class TimestampIndexError(CheckpointError):
    """frame PTS 缺失、重複或非單調。"""

    exit_code = 6


class DecodeAlignmentError(CheckpointError):
    """ffprobe frame ordinal 與 OpenCV 順序解碼無法一對一對齊。"""

    exit_code = 7


class RoiApprovalError(CheckpointError):
    """ROI approval 的輸入或 hash 驗證失敗。"""

    exit_code = 8

