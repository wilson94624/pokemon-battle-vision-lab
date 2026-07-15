"""以 magic bytes 為權威的輸入辨識與一致影像輸出。"""

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np

from .errors import InputError
from .utils import sha256_file


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"


def detect_image_format_bytes(data: bytes) -> Optional[str]:
    if data.startswith(PNG_MAGIC):
        return "png"
    if data.startswith(JPEG_MAGIC):
        return "jpeg"
    return None


def declared_image_format(path: Path) -> Optional[str]:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return "png"
    if suffix in (".jpg", ".jpeg"):
        return "jpeg"
    return None


def read_image(path: Path) -> Tuple[np.ndarray, Dict[str, Any]]:
    if not path.is_file():
        raise InputError("找不到圖片：{}".format(path))
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise InputError("無法讀取圖片 {}：{}".format(path, exc)) from exc
    detected = detect_image_format_bytes(data)
    if detected is None:
        raise InputError("圖片 magic bytes 不是支援的 PNG/JPEG：{}".format(path))
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise InputError("OpenCV 無法解碼圖片：{}".format(path))
    declared = declared_image_format(path)
    warnings = []
    if declared != detected:
        warnings.append(
            {
                "code": "INPUT_FORMAT_MISMATCH",
                "message": "副檔名宣告 {}，magic bytes 實際為 {}。".format(declared, detected),
            }
        )
    height, width = image.shape[:2]
    report = {
        "path": str(path),
        "sha256": sha256_file(path),
        "declared_format": declared,
        "detected_format": detected,
        "readable": True,
        "width": int(width),
        "height": int(height),
        "channels": 1 if image.ndim == 2 else int(image.shape[2]),
        "warnings": warnings,
    }
    return image, report


def encode_image(image: np.ndarray, encoding: str, jpeg_quality: int = 90) -> bytes:
    encoding = encoding.lower()
    if encoding == "png":
        extension = ".png"
        parameters = [cv2.IMWRITE_PNG_COMPRESSION, 3]
    elif encoding in ("jpg", "jpeg"):
        extension = ".jpg"
        parameters = [cv2.IMWRITE_JPEG_QUALITY, int(jpeg_quality)]
        encoding = "jpeg"
    else:
        raise InputError("不支援的輸出影像格式：{}".format(encoding))
    success, encoded = cv2.imencode(extension, image, parameters)
    if not success:
        raise InputError("OpenCV 無法編碼 {} 影像".format(encoding))
    data = encoded.tobytes()
    if detect_image_format_bytes(data) != encoding:
        raise InputError("輸出影像編碼後 magic bytes 驗證失敗")
    return data


def write_image(path: Path, image: np.ndarray, jpeg_quality: int = 90) -> Dict[str, Any]:
    declared = declared_image_format(path)
    if declared is None:
        raise InputError("輸出圖片副檔名必須是 .png/.jpg/.jpeg：{}".format(path))
    data = encode_image(image, declared, jpeg_quality=jpeg_quality)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    detected = detect_image_format_bytes(path.read_bytes()[:8])
    if detected != declared:
        raise InputError("寫入後副檔名與 magic bytes 不一致：{}".format(path))
    height, width = image.shape[:2]
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "encoding": detected,
        "width": int(width),
        "height": int(height),
    }
