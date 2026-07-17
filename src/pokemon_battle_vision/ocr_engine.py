"""本機 OCR engine adapter；正式實作使用 macOS Apple Vision。"""

import hashlib
import json
import os
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from .checkpoint1c_models import OcrEngineResult
from .errors import DependencyError


APPLE_VISION_ENGINE = "apple_vision_vnrecognizetextrequest"
APPLE_VISION_REVISION = "VNRecognizeTextRequestRevision3"
APPLE_VISION_LANGUAGE = "zh-Hant"


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def _runtime_probe_png() -> bytes:
    """建立固定 RGB PNG；probe 必須實際進入與 production 相同的 Vision path。"""

    width, height = 320, 96
    background = bytes((24, 32, 43)) * width
    foreground = bytes((244, 244, 244)) * 176
    rows = []
    for y in range(height):
        row = bytearray(background)
        if 34 <= y < 43 or 54 <= y < 63:
            row[72 * 3 : 248 * 3] = foreground
        rows.append(b"\x00" + bytes(row))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(b"".join(rows), level=9))
        + _png_chunk(b"IEND", b"")
    )


class AppleVisionOcrEngine:
    """以小型 Objective-C batch helper 隔離 Vision framework invocation。"""

    def __init__(self, source_path: Path = None, timeout_sec: float = 1800.0) -> None:
        self.source_path = source_path or (
            Path(__file__).resolve().parent / "resources/apple_vision_ocr.m"
        )
        self.timeout_sec = timeout_sec
        self._binary_path = None  # type: Path

    def _binary(self) -> Path:
        if self._binary_path is not None and self._binary_path.is_file():
            return self._binary_path
        if os.uname().sysname != "Darwin":
            raise DependencyError("Apple Vision OCR 只支援 macOS")
        if not self.source_path.is_file():
            raise DependencyError("找不到 Apple Vision OCR helper source：{}".format(self.source_path))
        digest = hashlib.sha256(self.source_path.read_bytes()).hexdigest()[:16]
        cache_dir = Path(tempfile.gettempdir()) / "pokemon-battle-vision-ocr"
        cache_dir.mkdir(parents=True, exist_ok=True)
        binary = cache_dir / "apple_vision_ocr_{}".format(digest)
        if not binary.is_file():
            command = [
                "xcrun",
                "clang",
                "-fobjc-arc",
                "-fblocks",
                "-framework",
                "Foundation",
                "-framework",
                "Vision",
                "-framework",
                "ImageIO",
                "-framework",
                "CoreGraphics",
                str(self.source_path),
                "-o",
                str(binary),
            ]
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=120.0,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise DependencyError("無法執行 xcrun clang：{}".format(exc)) from exc
            if completed.returncode != 0:
                raise DependencyError(
                    "Apple Vision OCR helper 編譯失敗：{}".format(
                        (completed.stderr or completed.stdout).strip()
                    )
                )
        self._binary_path = binary
        return binary

    def probe(self) -> Dict[str, Any]:
        try:
            completed = subprocess.run(
                [str(self._binary()), "--probe"],
                check=False,
                capture_output=True,
                text=True,
                timeout=60.0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DependencyError("Apple Vision OCR availability probe 失敗：{}".format(exc)) from exc
        if completed.returncode != 0 or not completed.stdout.strip():
            raise DependencyError(
                "Apple Vision OCR availability probe 失敗：{}".format(
                    (completed.stderr or "沒有輸出").strip()
                )
            )
        try:
            payload = json.loads(completed.stdout.splitlines()[-1])
        except json.JSONDecodeError as exc:
            raise DependencyError("Apple Vision OCR probe 回傳非 JSON") from exc
        if not payload.get("available"):
            raise DependencyError("Apple Vision 不支援固定繁體中文語言 zh-Hant")
        # capability query 不足以證明 production runtime 可配置 pixel buffer／模型。
        # 使用同一個 recognize() batch path 執行一張固定影像，避免假陽性 probe。
        with tempfile.TemporaryDirectory(prefix="pokemon-battle-vision-probe-") as directory:
            image_path = Path(directory) / "runtime-probe.png"
            image_path.write_bytes(_runtime_probe_png())
            result = self.recognize(
                [{"job_id": "apple-vision-runtime-probe", "image_path": str(image_path)}]
            )[0]
        if result.error is not None:
            raise DependencyError(
                "Apple Vision production runtime probe 失敗（與 production 共用 recognize path）：{}".format(
                    result.error
                )
            )
        payload.update(
            {
                "runtime_path_verified": True,
                "runtime_probe_job_id": result.job_id,
                "runtime_probe_error": None,
                "runtime_probe_result_count": len(result.lines),
            }
        )
        return payload

    def recognize(self, jobs: Sequence[Mapping[str, str]]) -> List[OcrEngineResult]:
        if not jobs:
            return []
        lines = [
            json.dumps(
                {"job_id": str(job["job_id"]), "image_path": str(job["image_path"])},
                ensure_ascii=False,
                sort_keys=True,
            )
            for job in jobs
        ]
        try:
            completed = subprocess.run(
                [str(self._binary())],
                input="\n".join(lines) + "\n",
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DependencyError("Apple Vision batch OCR 執行失敗：{}".format(exc)) from exc
        if completed.returncode != 0:
            raise DependencyError(
                "Apple Vision batch OCR 失敗：{}".format(
                    (completed.stderr or completed.stdout).strip()
                )
            )
        payloads = []
        for line in completed.stdout.splitlines():
            try:
                payloads.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise DependencyError("Apple Vision batch OCR 回傳非 JSONL") from exc
        if len(payloads) != len(jobs):
            raise DependencyError(
                "Apple Vision OCR 回傳數量不符：預期 {}，實際 {}".format(
                    len(jobs), len(payloads)
                )
            )
        return [
            OcrEngineResult(
                job_id=str(payload.get("job_id", "")),
                raw_text=str(payload.get("raw_text", "")),
                confidence=round(float(payload.get("confidence", 0.0)), 6),
                lines=list(payload.get("lines", [])),
                error=(None if payload.get("error") is None else str(payload["error"])),
            )
            for payload in payloads
        ]
