"""FFmpeg／ffprobe dependency、metadata 與 frame PTS 權威資料。"""

import json
import math
import platform
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np

from .errors import DependencyError, MediaProbeError
from .models import FrameTimestampIndex, VideoProfile


TESTED_FFMPEG_VERSION = "8.1.2"
CommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess]


def _default_runner(command: Sequence[str], timeout_sec: float) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(command),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaProbeError("外部 command timeout（{} 秒）：{}".format(timeout_sec, command[0])) from exc
    except OSError as exc:
        raise MediaProbeError("無法執行 {}：{}".format(command[0], exc)) from exc


def _version_from_output(executable_name: str, output: str) -> str:
    match = re.search(r"^{} version\s+([^\s]+)".format(re.escape(executable_name)), output, re.MULTILINE)
    if not match:
        raise DependencyError("無法解析 {} -version 輸出".format(executable_name))
    return match.group(1)


def normalize_rotation(value: Any) -> int:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise MediaProbeError("rotation metadata 不是數字：{}".format(value)) from exc
    ffprobe_counter_clockwise = int(round(numeric)) % 360
    if ffprobe_counter_clockwise not in (0, 90, 180, 270) or not math.isclose(
        numeric % 360, ffprobe_counter_clockwise, abs_tol=0.5
    ):
        raise MediaProbeError("僅支援 0/90/180/270 度 rotation，實際為 {}".format(value))
    # ffprobe Display Matrix 的正值為 counter-clockwise；內部一律改存 clockwise canonical。
    return (-ffprobe_counter_clockwise) % 360


def dependency_preflight(
    timeout_sec: float = 15.0,
    runner: CommandRunner = _default_runner,
    which: Callable[[str], Optional[str]] = shutil.which,
) -> Dict[str, Any]:
    executable_paths = {}
    versions = {}
    full_version_lines = {}
    warnings = []
    for name in ("ffmpeg", "ffprobe"):
        executable = which(name)
        if not executable:
            raise DependencyError("找不到 {}；macOS 請先執行 `brew install ffmpeg`".format(name))
        executable_paths[name] = executable
        result = runner([executable, "-version"], timeout_sec)
        if result.returncode != 0:
            raise DependencyError("{} -version 失敗：{}".format(name, result.stderr.strip()))
        versions[name] = _version_from_output(name, result.stdout)
        full_version_lines[name] = result.stdout.splitlines()[0] if result.stdout else ""

    if versions["ffmpeg"] != versions["ffprobe"]:
        raise DependencyError(
            "ffmpeg 與 ffprobe 版本來源不一致：{} vs {}".format(
                versions["ffmpeg"], versions["ffprobe"]
            )
        )
    if versions["ffprobe"] != TESTED_FFMPEG_VERSION:
        warnings.append(
            {
                "code": "UNTESTED_FFMPEG_VERSION",
                "message": "目前版本 {}，已測試版本為 {}；capability probe 通過後繼續。".format(
                    versions["ffprobe"], TESTED_FFMPEG_VERSION
                ),
            }
        )

    # 使用 lavfi 產生 2×2、單影格的記憶體輸入，確認 JSON 與所需 frame PTS 欄位存在。
    capability_command = [
        executable_paths["ffprobe"],
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=size=2x2:rate=1:duration=1",
        "-show_streams",
        "-show_frames",
        "-show_entries",
        "stream=codec_type,width,height:frame=best_effort_timestamp_time",
        "-of",
        "json",
    ]
    result = runner(capability_command, timeout_sec)
    if result.returncode != 0:
        raise DependencyError("ffprobe capability probe 失敗：{}".format(result.stderr.strip()))
    try:
        capability = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DependencyError("ffprobe capability probe 回傳損壞的 JSON") from exc
    streams = capability.get("streams")
    frames = capability.get("frames")
    if not streams or streams[0].get("width") != 2 or not frames:
        raise DependencyError("ffprobe capability probe 缺少 stream/frame 必要欄位")
    if "best_effort_timestamp_time" not in frames[0]:
        raise DependencyError("ffprobe capability probe 缺少 best_effort_timestamp_time")

    return {
        "schema_version": "0.1.0",
        "status": "pass",
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "executables": executable_paths,
        "versions": versions,
        "version_lines": full_version_lines,
        "tested_ffmpeg_version": TESTED_FFMPEG_VERSION,
        "capability_probe": {
            "status": "pass",
            "json_output": True,
            "best_effort_timestamp_time": True,
        },
        "warnings": warnings,
    }


def _run_ffprobe_json(
    ffprobe_path: str,
    arguments: Sequence[str],
    timeout_sec: float,
    runner: CommandRunner = _default_runner,
) -> Dict[str, Any]:
    command = [ffprobe_path, "-v", "error"] + list(arguments) + ["-of", "json"]
    result = runner(command, timeout_sec)
    if result.returncode != 0:
        message = result.stderr.strip() or "未提供 stderr"
        raise MediaProbeError("ffprobe 失敗（exit {}）：{}".format(result.returncode, message))
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MediaProbeError("ffprobe 回傳損壞的 JSON：{}".format(exc)) from exc
    if not isinstance(payload, dict):
        raise MediaProbeError("ffprobe JSON 根節點不是 object")
    return payload


def parse_metadata_payload(
    payload: Dict[str, Any],
    video_path: Path,
    video_sha256: str,
    profile: VideoProfile,
    environment_report: Dict[str, Any],
) -> Dict[str, Any]:
    streams = payload.get("streams")
    format_data = payload.get("format")
    if not isinstance(streams, list) or not isinstance(format_data, dict):
        raise MediaProbeError("ffprobe metadata 缺少 streams 或 format")
    video_streams = [row for row in streams if row.get("codec_type") == "video"]
    if not video_streams:
        raise MediaProbeError("輸入檔沒有 video stream")
    stream = video_streams[0]
    required = ("index", "codec_name", "width", "height", "time_base")
    missing = [name for name in required if name not in stream]
    if missing:
        raise MediaProbeError("video stream 缺少必要欄位：{}".format(", ".join(missing)))
    try:
        encoded_width = int(stream["width"])
        encoded_height = int(stream["height"])
    except (TypeError, ValueError) as exc:
        raise MediaProbeError("encoded dimensions 無效") from exc

    rotation_raw = 0
    rotation_source = "default"
    for side_data in stream.get("side_data_list", []):
        if "rotation" in side_data:
            rotation_raw = side_data["rotation"]
            rotation_source = str(side_data.get("side_data_type", "side_data_list"))
            break
    rotation = normalize_rotation(rotation_raw)
    ffprobe_counter_clockwise = int(round(float(rotation_raw))) % 360
    if rotation in (90, 270):
        display_width, display_height = encoded_height, encoded_width
    else:
        display_width, display_height = encoded_width, encoded_height

    warnings: List[Dict[str, str]] = list(environment_report.get("warnings", []))
    expected_match = display_width == profile.display_width and display_height == profile.display_height
    if not expected_match:
        warnings.append(
            {
                "code": "DISPLAY_RESOLUTION_MISMATCH",
                "message": "rotation 後 display resolution 為 {}×{}，唯一支援規格為 {}×{}。".format(
                    display_width, display_height, profile.display_width, profile.display_height
                ),
            }
        )
    reported_frames = stream.get("nb_frames")
    try:
        reported_frames_value = int(reported_frames) if reported_frames is not None else None
    except (TypeError, ValueError):
        reported_frames_value = None
        warnings.append({"code": "INVALID_REPORTED_FRAME_COUNT", "message": "ffprobe nb_frames 無法解析。"})

    def optional_float(value: Any) -> Optional[float]:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "schema_version": "0.1.0",
        "probe_source": "ffprobe",
        "source_video": str(video_path),
        "video_sha256": video_sha256,
        "ffmpeg_version": environment_report["versions"]["ffmpeg"],
        "ffprobe_version": environment_report["versions"]["ffprobe"],
        "stream_index": int(stream["index"]),
        "codec": str(stream["codec_name"]),
        "codec_long_name": stream.get("codec_long_name"),
        "pixel_format": stream.get("pix_fmt"),
        "encoded_dimensions": {"width": encoded_width, "height": encoded_height},
        "coded_dimensions": {
            "width": int(stream.get("coded_width", encoded_width)),
            "height": int(stream.get("coded_height", encoded_height)),
        },
        "rotation": {
            "raw": rotation_raw,
            "ffprobe_counter_clockwise_degrees": ffprobe_counter_clockwise,
            "clockwise_degrees": rotation,
            "source": rotation_source,
        },
        "display_dimensions": {"width": display_width, "height": display_height},
        "opencv_decoded_dimensions": None,
        "opencv_display_dimensions_after_manual_rotation": None,
        "opencv_backend": None,
        "opencv_orientation_auto_disabled": None,
        "time_base": str(stream["time_base"]),
        "nominal_frame_rate": stream.get("r_frame_rate"),
        "average_frame_rate": stream.get("avg_frame_rate"),
        "container_duration_sec": optional_float(format_data.get("duration")),
        "video_stream_duration_sec": optional_float(stream.get("duration")),
        "container_format": format_data.get("format_name"),
        "container_size_bytes": int(format_data["size"]) if str(format_data.get("size", "")).isdigit() else None,
        "container_bit_rate": int(format_data["bit_rate"]) if str(format_data.get("bit_rate", "")).isdigit() else None,
        "ffprobe_reported_frame_count": reported_frames_value,
        "pts_index_frame_count": None,
        "vfr": None,
        "profile": profile.to_dict(),
        "expected_resolution_match": expected_match,
        "warnings": warnings,
    }


def probe_metadata(
    video_path: Path,
    video_sha256: str,
    profile: VideoProfile,
    environment_report: Dict[str, Any],
    timeout_sec: float = 60.0,
    runner: CommandRunner = _default_runner,
) -> Dict[str, Any]:
    payload = _run_ffprobe_json(
        environment_report["executables"]["ffprobe"],
        ["-show_streams", "-show_format", str(video_path)],
        timeout_sec,
        runner,
    )
    return parse_metadata_payload(payload, video_path, video_sha256, profile, environment_report)


def _vfr_diagnostics(pts: np.ndarray) -> Dict[str, Any]:
    if pts.size < 2:
        return {
            "is_vfr": False,
            "delta_count": 0,
            "min_delta_sec": None,
            "median_delta_sec": None,
            "max_delta_sec": None,
            "outlier_fraction": 0.0,
            "top_delta_buckets": [],
        }
    deltas = np.diff(pts)
    median = float(np.median(deltas))
    tolerance = max(1e-6, abs(median) * 0.05)
    outlier_fraction = float(np.mean(np.abs(deltas - median) > tolerance))
    positive = deltas[deltas > 0]
    ratio = float(np.max(positive) / np.min(positive)) if positive.size else float("inf")
    buckets = Counter(round(float(value), 6) for value in deltas)
    top = [
        {"delta_sec": delta, "count": count}
        for delta, count in sorted(buckets.items(), key=lambda item: (-item[1], item[0]))[:12]
    ]
    return {
        "is_vfr": bool(outlier_fraction > 0.001 or ratio > 1.10),
        "delta_count": int(deltas.size),
        "min_delta_sec": float(np.min(deltas)),
        "median_delta_sec": median,
        "max_delta_sec": float(np.max(deltas)),
        "outlier_fraction": outlier_fraction,
        "delta_ratio": ratio,
        "tolerance_sec": tolerance,
        "top_delta_buckets": top,
    }


def parse_frame_timestamp_payload(
    payload: Dict[str, Any], video_sha256: str, ffprobe_version: str
) -> FrameTimestampIndex:
    frames = payload.get("frames")
    if not isinstance(frames, list):
        raise MediaProbeError("ffprobe frame JSON 缺少 frames array")
    pts_values = []
    durations = []
    key_frames = []
    missing_ordinals = []
    for ordinal, frame in enumerate(frames):
        raw_pts = frame.get("best_effort_timestamp_time") if isinstance(frame, dict) else None
        try:
            pts = float(raw_pts)
        except (TypeError, ValueError):
            missing_ordinals.append(ordinal)
            continue
        pts_values.append(pts)
        try:
            duration = float(frame.get("pkt_duration_time", "nan"))
        except (TypeError, ValueError):
            duration = float("nan")
        durations.append(duration)
        key_frames.append(bool(int(frame.get("key_frame", 0))))
    pts_array = np.asarray(pts_values, dtype=np.float64)
    duration_array = np.asarray(durations, dtype=np.float64)
    key_frame_array = np.asarray(key_frames, dtype=np.bool_)
    deltas = np.diff(pts_array) if pts_array.size > 1 else np.asarray([], dtype=np.float64)
    duplicate_indices = (np.where(deltas == 0)[0] + 1).astype(int).tolist()
    non_monotonic_indices = (np.where(deltas < 0)[0] + 1).astype(int).tolist()
    validation = {
        "source_frame_count": len(frames),
        "parsed_pts_count": int(pts_array.size),
        "complete": not missing_ordinals,
        "strictly_monotonic": not duplicate_indices and not non_monotonic_indices,
        "missing_count": len(missing_ordinals),
        "missing_ordinals_preview": missing_ordinals[:20],
        "duplicate_count": len(duplicate_indices),
        "duplicate_ordinals_preview": duplicate_indices[:20],
        "non_monotonic_count": len(non_monotonic_indices),
        "non_monotonic_ordinals_preview": non_monotonic_indices[:20],
        "vfr_diagnostics": _vfr_diagnostics(pts_array),
    }
    return FrameTimestampIndex(
        pts_sec=pts_array,
        duration_sec=duration_array,
        key_frame=key_frame_array,
        validation=validation,
        video_sha256=video_sha256,
        ffprobe_version=ffprobe_version,
    )


def probe_frame_timestamps(
    video_path: Path,
    video_sha256: str,
    environment_report: Dict[str, Any],
    timeout_sec: float = 180.0,
    runner: CommandRunner = _default_runner,
) -> FrameTimestampIndex:
    payload = _run_ffprobe_json(
        environment_report["executables"]["ffprobe"],
        [
            "-select_streams",
            "v:0",
            "-show_frames",
            "-show_entries",
            "frame=best_effort_timestamp_time,pkt_duration_time,key_frame",
            str(video_path),
        ],
        timeout_sec,
        runner,
    )
    return parse_frame_timestamp_payload(
        payload, video_sha256, environment_report["versions"]["ffprobe"]
    )


def save_frame_timestamp_index(path: Path, index: FrameTimestampIndex) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata_json = json.dumps(
        {
            "schema_version": "0.1.0",
            "authority": "ffprobe.best_effort_timestamp_time",
            "video_sha256": index.video_sha256,
            "ffprobe_version": index.ffprobe_version,
            "validation": index.validation,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    np.savez_compressed(
        str(path),
        ordinal=np.arange(index.frame_count, dtype=np.int64),
        pts_sec=index.pts_sec,
        duration_sec=index.duration_sec,
        key_frame=index.key_frame,
        metadata_json=np.asarray(metadata_json),
    )
